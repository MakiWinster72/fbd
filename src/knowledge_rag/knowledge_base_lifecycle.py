from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from knowledge_rag.admin_store import AdminStore
from knowledge_rag.db import (
    DocumentImportRecord,
    IndexJobRecord,
    KnowledgeBaseRecord,
    UserKnowledgeBaseRecord,
)
from knowledge_rag.document_leases import DocumentLease, try_acquire
from knowledge_rag.documents import stable_id
from knowledge_rag.index_commit import atomic_json
from knowledge_rag.milvus_store import MilvusChunkStore
from knowledge_rag.pipeline import IngestionPipeline

if TYPE_CHECKING:
    from knowledge_rag.document_import import DocumentImportManager


@contextmanager
def index_lifecycle_gate(engine: Engine) -> Iterator[DocumentLease]:
    lease = try_acquire(engine, "__index_lifecycle__", "maintenance")
    if lease is None:
        raise ValueError("索引维护正在进行，请稍后重试")
    try:
        yield lease
    finally:
        lease.close()


@contextmanager
def knowledge_base_gate(engine: Engine, knowledge_base_id: str, require_active: bool = True) -> Iterator[DocumentLease]:
    lease = try_acquire(engine, "__knowledge_base_lifecycle__", knowledge_base_id)
    if lease is None:
        raise ValueError("知识库正在处理其他操作，请稍后重试")
    try:
        with Session(engine) as session:
            row = session.get(KnowledgeBaseRecord, knowledge_base_id)
            if row is None:
                raise KeyError(knowledge_base_id)
            if require_active and not row.active:
                raise ValueError("知识库已停用，不能继续写入资料")
        yield lease
    finally:
        lease.close()


def begin_delete(store: AdminStore, knowledge_base_id: str, confirmation: str, actor_id: str) -> None:
    with Session(store.engine) as session, session.begin():
        row = session.get(KnowledgeBaseRecord, knowledge_base_id)
        if row is None:
            raise KeyError(knowledge_base_id)
        if confirmation != f"我确认删除({row.name})":
            raise ValueError("确认文字不匹配")
        active_import = session.scalar(select(DocumentImportRecord.id).where(DocumentImportRecord.knowledge_base_id == knowledge_base_id, DocumentImportRecord.status.in_(["queued", "parsing", "embedding"])).limit(1))
        active_index = session.scalar(select(IndexJobRecord.id).where(IndexJobRecord.status.in_(["queued", "running"])).limit(1))
        if active_import or active_index:
            raise ValueError("仍有导入或索引任务运行，请完成后再删除")
        row.active = False
        row.updated_at = datetime.now(UTC).isoformat()
        # Keep the row to avoid cascading deletion of user-owned conversations.
        session.execute(delete(UserKnowledgeBaseRecord).where(UserKnowledgeBaseRecord.knowledge_base_id == knowledge_base_id))
        store._audit(session, actor_id, "knowledge_base.deleting", "knowledge_base", knowledge_base_id, {})


def delete_knowledge_base(manager: DocumentImportManager, knowledge_base_id: str, confirmation: str, actor_id: str) -> None:
    store = manager.store
    with index_lifecycle_gate(store.engine), knowledge_base_gate(store.engine, knowledge_base_id, require_active=False) as gate:
        begin_delete(store, knowledge_base_id, confirmation, actor_id)
        # A failed cleanup leaves the base inactive; the same request can be retried.
        with Session(store.engine) as session:
            jobs = list(session.scalars(select(DocumentImportRecord).where(DocumentImportRecord.knowledge_base_id == knowledge_base_id)))
            collections = set(session.scalars(select(IndexJobRecord.collection_name)))
        active = manager._milvus()
        collections.update([active.collection, manager.settings.milvus_collection])
        stores = [active if name == active.collection else MilvusChunkStore(manager.settings.milvus_uri, name, active.vector_dim) for name in sorted(collections)]
        document_ids = {stable_id("document", job.file_hash) for job in jobs}
        for vector_store in stores:
            document_ids.update(str(row["document_id"]) for row in vector_store.document_chunks(knowledge_base_id))
        normalized = manager.settings.data_dir / "normalized"
        for path in normalized.glob("*/chunks.json"):
            if path.is_symlink() or path.parent.is_symlink():
                raise ValueError("缓存包含符号链接，已拒绝删除")
            try:
                chunks = json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                raise ValueError("无法读取文档缓存，修复后可重试删除") from exc
            document_ids.update(str(item["document_id"]) for item in chunks if item.get("knowledge_base_id") == knowledge_base_id)
        for document_id in sorted(document_ids):
            # Stable IDs also guard against paths escaping the normalized directory.
            if len(document_id) != 64 or any(c not in "0123456789abcdef" for c in document_id):
                raise ValueError("文档标识无效，已拒绝删除")
            lease = try_acquire(store.engine, "__shared_document__", document_id)
            if lease is None:
                raise ValueError("文档正在处理，请稍后重试删除")
            try:
                gate.ensure_owned()
                lease.ensure_owned()
                for vector_store in stores:
                    if vector_store.has_collection():
                        vector_store.delete_document(knowledge_base_id, document_id)
                directory = normalized / document_id
                IngestionPipeline.remove_cached_chunks(directory, knowledge_base_id, document_id)
                vectors_path = directory / "chunks-with-vectors.json"
                if vectors_path.is_file():
                    if vectors_path.is_symlink():
                        raise ValueError("缓存包含符号链接，已拒绝删除")
                    try:
                        cached_vectors = json.loads(vectors_path.read_text())
                    except (OSError, ValueError) as exc:
                        raise ValueError("无法读取向量缓存，修复后可重试删除") from exc
                    atomic_json(vectors_path, [item for item in cached_vectors if item.get("knowledge_base_id") != knowledge_base_id])
                chunks_path = directory / "chunks.json"
                if chunks_path.is_file() and not json.loads(chunks_path.read_text()):
                    shutil.rmtree(directory)
                for job in jobs:
                    if stable_id("document", job.file_hash) != document_id:
                        continue
                    directory = manager.settings.data_dir / "uploads" / job.id
                    if directory.exists():
                        if directory.is_symlink() or directory.parent.resolve() != (manager.settings.data_dir / "uploads").resolve():
                            raise ValueError("上传目录路径无效")
                        allowed = {job.file_name, "options.json"}
                        if any(p.name not in allowed or p.is_symlink() or not p.is_file() for p in directory.iterdir()):
                            raise ValueError("上传目录包含未知文件，已拒绝删除")
                        for p in directory.iterdir():
                            p.unlink()
                        directory.rmdir()
                    manager.update(job.id, status="deleted")
            finally:
                lease.close()
        gate.ensure_owned()
        with Session(store.engine) as session, session.begin():
            store._audit(session, actor_id, "knowledge_base.delete", "knowledge_base", knowledge_base_id, {"history_retained": True})


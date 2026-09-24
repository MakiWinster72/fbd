from __future__ import annotations

from knowledge_rag.documents import sha256_file, stable_id
import json
import mimetypes
import threading
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from knowledge_rag.admin_store import AdminStore
from knowledge_rag.config import Settings
from knowledge_rag.db import DocumentImportRecord
from knowledge_rag.milvus_store import MilvusChunkStore
from knowledge_rag.pipeline import IngestionPipeline
from knowledge_rag.providers import create_embedder


def now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ImportJob:
    id: str
    file_name: str
    knowledge_base_id: str
    file_hash: str
    status: str
    completed_chunks: int
    total_chunks: int
    error: str
    created_at: str
    updated_at: str


def to_job(record: DocumentImportRecord) -> ImportJob:
    return ImportJob(**{name: getattr(record, name) for name in ImportJob.__dataclass_fields__})


def parse_mineru(path: Path, output: Path, base_url: str, force_ocr: bool) -> Path:
    """Call the project's local MinerU Gradio stream and persist its content list."""
    base = base_url.rstrip("/")
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    boundary = uuid4().hex
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"document{path.suffix}\"\r\nContent-Type: {mime}\r\n\r\n".encode()
            + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode())
    request = urllib.request.Request(f"{base}/gradio_api/upload", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with client.open(request, timeout=120) as response:
        uploaded = json.load(response)[0]
    payload = {"data": [
        {"path": uploaded, "orig_name": path.name, "mime_type": mime, "meta": {"_type": "gradio.FileData"}},
        1000, force_ocr, True, True, True, "medium",
        "ch (Chinese, English, Japanese, Chinese Traditional, Latin)", "hybrid-engine", "http://localhost:30000",
    ]}
    request = urllib.request.Request(f"{base}/gradio_api/call/convert_to_markdown_stream", json.dumps(payload).encode(), {"Content-Type": "application/json"})
    with client.open(request, timeout=30) as response:
        event_id = json.load(response)["event_id"]
    request = urllib.request.Request(f"{base}/gradio_api/call/convert_to_markdown_stream/{event_id}", headers={"Accept": "text/event-stream"})
    result = None
    event = ""
    with client.open(request, timeout=900) as response:
        for line in response:
            text = line.decode("utf-8", "replace").strip()
            if text.startswith("event:"):
                event = text[6:].strip()
            elif text.startswith("data:"):
                if event == "error":
                    raise RuntimeError(f"MinerU 解析失败: {text[5:][:500]}")
                if event == "complete":
                    result = json.loads(text[5:].strip())
                    break
    if not isinstance(result, list) or len(result) < 5 or not isinstance(result[4], str):
        raise RuntimeError("MinerU 未返回 content_list.json")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "content_list.json"
    destination.write_text(result[4])
    return destination


class DocumentImportManager:
    def __init__(self, settings: Settings, store: AdminStore) -> None:
        self.settings = settings
        self.store = store
        with Session(store.engine) as session, session.begin():
            session.execute(update(DocumentImportRecord).where(DocumentImportRecord.status.in_(["queued", "parsing", "embedding"])).values(status="failed", error="服务重启导致任务中断", updated_at=now()))

    def get(self, job_id: str) -> ImportJob | None:
        with Session(self.store.engine) as session:
            record = session.get(DocumentImportRecord, job_id)
            return to_job(record) if record else None

    def list(self) -> list[ImportJob]:
        with Session(self.store.engine) as session:
            return [to_job(item) for item in session.scalars(select(DocumentImportRecord).order_by(DocumentImportRecord.created_at.desc()).limit(50))]

    def update(self, job_id: str, **values: object) -> None:
        with Session(self.store.engine) as session, session.begin():
            session.execute(update(DocumentImportRecord).where(DocumentImportRecord.id == job_id).values(**values, updated_at=now()))

    def start(self, path: Path, file_name: str, knowledge_base_id: str, ocr_mode: str, duplicate_mode: str) -> ImportJob:
        digest = sha256_file(path)
        with Session(self.store.engine) as session, session.begin():
            previous = session.scalars(select(DocumentImportRecord).where(
                DocumentImportRecord.knowledge_base_id == knowledge_base_id,
                DocumentImportRecord.file_hash == digest,
                DocumentImportRecord.status.in_(["queued", "parsing", "embedding", "completed"]),
            )).first()
            if previous and duplicate_mode == "skip":
                path.unlink(missing_ok=True)
                path.parent.rmdir()
                return to_job(previous)
            job_id = str(uuid4())
            stamp = now()
            record = DocumentImportRecord(id=job_id, file_name=file_name, knowledge_base_id=knowledge_base_id,
                                          file_hash=digest, status="queued", completed_chunks=0, total_chunks=0,
                                          error="", created_at=stamp, updated_at=stamp)
            session.add(record)
            result = to_job(record)
        thread = threading.Thread(target=self._run, args=(job_id, path, ocr_mode), daemon=True, name=f"document-import-{job_id[:8]}")
        thread.start()
        return result

    def _milvus(self) -> MilvusChunkStore:
        collection = self.store.get_setting("active_collection_name", self.settings.milvus_collection)
        dim = int(self.store.get_setting("active_vector_dim", str(self.settings.milvus_vector_dim)))
        return MilvusChunkStore(self.settings.milvus_uri, collection, dim)

    def documents(self, knowledge_base_id: str) -> list[dict[str, object]]:
        rows = self._milvus().document_chunks(knowledge_base_id)
        documents: dict[str, dict[str, object]] = {}
        for row in rows:
            doc_id = str(row["document_id"])
            entry = documents.setdefault(doc_id, {"document_id": doc_id, "document_name": row.get("document_name") or "未知文档", "chunk_count": 0, "page_count": 0})
            entry["chunk_count"] = int(entry["chunk_count"]) + 1
            entry["page_count"] = max(int(entry["page_count"]), int(row.get("page_end") or 0))
        return sorted(documents.values(), key=lambda item: str(item["document_name"]))

    def document(self, knowledge_base_id: str, document_id: str, include_content: bool = True) -> dict[str, object] | None:
        rows = self._milvus().document_chunks(knowledge_base_id, document_id, include_content=include_content)
        if not rows:
            return None
        return {
            "document_id": document_id,
            "document_name": rows[0].get("document_name") or "未知文档",
            "chunk_count": len(rows),
            "page_count": max(int(row.get("page_end") or 0) for row in rows),
            "chunks": sorted([{"page_start": row.get("page_start") or 0, "page_end": row.get("page_end") or 0, "section_title": row.get("section_title") or "", "content": row.get("content") or ""} for row in rows], key=lambda item: int(item["page_start"])),
        }

    def _cached_chunks(self, knowledge_base_id: str, document_id: str) -> tuple[Path, list[dict]]:
        path = self.settings.data_dir / "normalized" / document_id / "chunks.json"
        if not path.is_file():
            return path, []
        chunks = json.loads(path.read_text())
        return path, [item for item in chunks if item.get("knowledge_base_id") == knowledge_base_id and item.get("document_id") == document_id]

    def delete_document(self, knowledge_base_id: str, document_id: str) -> bool:
        if self.document(knowledge_base_id, document_id, include_content=False) is None:
            return False
        digest_jobs = self._jobs_for_document(knowledge_base_id, document_id)
        if any(item.status in {"queued", "parsing", "embedding"} for item in digest_jobs):
            raise ValueError("文档正在导入，请完成后再删除")
        path, _ = self._cached_chunks(knowledge_base_id, document_id)
        self._milvus().delete_document(knowledge_base_id, document_id)
        if path.is_file():
            chunks = json.loads(path.read_text())
            remaining = [item for item in chunks if not (item.get("document_id") == document_id and item.get("knowledge_base_id") == knowledge_base_id)]
            path.write_text(json.dumps(remaining, ensure_ascii=False, indent=2))
        for item in digest_jobs:
            if item.status == "completed":
                self.update(item.id, status="deleted")
        return True

    def _jobs_for_document(self, knowledge_base_id: str, document_id: str) -> list[ImportJob]:
        with Session(self.store.engine) as session:
            records = session.scalars(select(DocumentImportRecord).where(DocumentImportRecord.knowledge_base_id == knowledge_base_id)).all()
            return [to_job(record) for record in records if stable_id("document", record.file_hash) == document_id]

    def reindex_document(self, knowledge_base_id: str, document_id: str) -> ImportJob:
        document = self.document(knowledge_base_id, document_id, include_content=False)
        if document is None:
            raise KeyError("文档不存在")
        _, chunks = self._cached_chunks(knowledge_base_id, document_id)
        if not chunks:
            raise ValueError("未保留可重新向量化的切块，请重新上传原文件")
        if any(item.status in {"queued", "parsing", "embedding"} for item in self._jobs_for_document(knowledge_base_id, document_id)):
            raise ValueError("文档已有进行中的任务")
        metadata = self.settings.data_dir / "normalized" / document_id / "document.json"
        file_hash = json.loads(metadata.read_text()).get("file_hash", "") if metadata.is_file() else ""
        if not file_hash:
            file_hash = next((item.file_hash for item in self._jobs_for_document(knowledge_base_id, document_id)), "")
        if not file_hash or stable_id("document", file_hash) != document_id:
            raise ValueError("文档缺少有效的源文件标识，请重新上传原文件")
        stamp = now()
        job_id = str(uuid4())
        with Session(self.store.engine) as session, session.begin():
            session.add(DocumentImportRecord(id=job_id, file_name=str(document["document_name"]), knowledge_base_id=knowledge_base_id,
                                             file_hash=file_hash,
                                             status="queued", completed_chunks=0, total_chunks=len(chunks), error="", created_at=stamp, updated_at=stamp))
        thread = threading.Thread(target=self._run_reindex, args=(job_id, document_id), daemon=True, name=f"document-reindex-{job_id[:8]}")
        thread.start()
        return self.get(job_id)  # type: ignore[return-value]

    def _run_reindex(self, job_id: str, document_id: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        try:
            profile = self.store.get_profile(self.store.get_setting("active_embedding_profile_id"))
            if profile is None or not profile.enabled:
                raise RuntimeError("尚未启用 Embedding 配置")
            collection = self.store.get_setting("active_collection_name", self.settings.milvus_collection)
            _, chunks = self._cached_chunks(job.knowledge_base_id, document_id)
            if not chunks:
                raise RuntimeError("缓存切块已不存在")
            from knowledge_rag.models import Chunk
            embedder = create_embedder(profile)
            milvus = self._milvus()
            self.update(job_id, status="embedding")
            batch_size = max(1, int(profile.options.get("batch_size", 8)))
            for offset in range(0, len(chunks), batch_size):
                if self.store.get_setting("active_collection_name", self.settings.milvus_collection) != collection:
                    raise RuntimeError("Embedding 索引已切换，请重新尝试")
                batch = chunks[offset:offset + batch_size]
                vectors = embedder.embed([str(item["content"]) for item in batch])
                milvus.upsert([Chunk(**{**item, "block_types": tuple(item.get("block_types") or ()), "vector": None}).with_vector(vector) for item, vector in zip(batch, vectors, strict=True)])
                self.update(job_id, completed_chunks=offset + len(batch))
            self.update(job_id, status="completed")
        except Exception as exc:
            self.update(job_id, status="failed", error=str(exc)[:1000])

    def _run(self, job_id: str, path: Path, ocr_mode: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        try:
            profile = self.store.get_profile(self.store.get_setting("active_embedding_profile_id"))
            if profile is None or not profile.enabled:
                raise RuntimeError("尚未启用 Embedding 配置")
            dim = int(self.store.get_setting("active_vector_dim", str(profile.vector_dim or 0)))
            if dim <= 0:
                raise RuntimeError("Embedding 向量维度未设置")
            collection = self.store.get_setting("active_collection_name", self.settings.milvus_collection)
            self.update(job_id, status="parsing")
            mineru_list = None
            if path.suffix.lower() == ".pdf" and ocr_mode != "native":
                mineru_list = parse_mineru(path, self.settings.data_dir / "mineru" / job_id, self.settings.mineru_base_url, ocr_mode == "force")
            pipeline = IngestionPipeline(self.settings)
            prepared = pipeline.prepare(path, job.knowledge_base_id, mineru_list, persist=False)
            if not prepared.chunks:
                raise RuntimeError("文档未提取出可索引内容")
            self.update(job_id, status="embedding", total_chunks=len(prepared.chunks))
            embedder = create_embedder(profile)
            milvus = MilvusChunkStore(self.settings.milvus_uri, collection, dim)
            batch_size = max(1, int(profile.options.get("batch_size", 8)))
            for offset in range(0, len(prepared.chunks), batch_size):
                # Detect a concurrent embedding switch before writing into the wrong collection.
                if self.store.get_setting("active_collection_name", self.settings.milvus_collection) != collection:
                    raise RuntimeError("Embedding 索引已切换，请重新导入")
                batch = prepared.chunks[offset:offset + batch_size]
                vectors = embedder.embed([chunk.content for chunk in batch])
                embedded = [chunk.with_vector(vector) for chunk, vector in zip(batch, vectors, strict=True)]
                milvus.upsert(embedded)
                self.update(job_id, completed_chunks=offset + len(batch))
            pipeline.save_prepared(prepared)
            self.update(job_id, status="completed")
        except Exception as exc:
            self.update(job_id, status="failed", error=str(exc)[:1000])
        finally:
            path.unlink(missing_ok=True)
            path.parent.rmdir()

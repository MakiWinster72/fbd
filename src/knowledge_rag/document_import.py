from __future__ import annotations

import json
import logging
import mimetypes
import threading
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from knowledge_rag.admin_store import AdminStore
from knowledge_rag.config import Settings
from knowledge_rag.db import DocumentImportRecord
from knowledge_rag.document_leases import DocumentLease, recover_expired, try_acquire
from knowledge_rag.documents import sha256_file, stable_id
from knowledge_rag.index_commit import IndexIntent, pending_intents
from knowledge_rag.knowledge_base_lifecycle import knowledge_base_gate
from knowledge_rag.milvus_store import MilvusChunkStore
from knowledge_rag.pipeline import IngestionPipeline
from knowledge_rag.providers import create_embedder

logger = logging.getLogger(__name__)
# Normalized chunks are shared by document hash across knowledge bases.
DOCUMENT_LEASE_SCOPE = "__shared_document__"


def now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class _DocumentSummary:
    document_id: str
    document_name: str
    chunk_count: int = 0
    page_count: int = 0


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
    source_available: bool


def retained_source(data_dir: Path, job_id: str, file_name: str) -> Path | None:
    if Path(file_name).name != file_name or file_name in {".", ".."}:
        return None
    return data_dir / "uploads" / job_id / file_name


def to_job(record: DocumentImportRecord, data_dir: Path) -> ImportJob:
    source = retained_source(data_dir, record.id, record.file_name)
    return ImportJob(
        **{name: getattr(record, name) for name in ImportJob.__dataclass_fields__ if name != "source_available"},
        source_available=record.status == "failed" and source is not None and source.is_file(),
    )


class _NoMineruRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl) -> None:
        return None


def parse_mineru(path: Path, output: Path, base_url: str, force_ocr: bool) -> Path:
    """Call the project's local MinerU Gradio stream and persist its content list."""
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("MinerU 地址必须是有效的 HTTP(S) 服务地址")
    base = base_url.rstrip("/")
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoMineruRedirect())
    boundary = uuid4().hex
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"document{path.suffix}\"\r\nContent-Type: {mime}\r\n\r\n".encode()
            + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode())
    request = urllib.request.Request(f"{base}/gradio_api/upload", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with client.open(request, timeout=120) as response:
        try:
            uploaded = json.load(response)[0]
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            raise RuntimeError("MinerU 上传返回无效结果") from exc
    payload = {"data": [
        {"path": uploaded, "orig_name": path.name, "mime_type": mime, "meta": {"_type": "gradio.FileData"}},
        1000, force_ocr, True, True, True, "medium",
        "ch (Chinese, English, Japanese, Chinese Traditional, Latin)", "hybrid-engine", "http://localhost:30000",
    ]}
    request = urllib.request.Request(f"{base}/gradio_api/call/convert_to_markdown_stream", json.dumps(payload).encode(), {"Content-Type": "application/json"})
    with client.open(request, timeout=30) as response:
        try:
            event_id = json.load(response)["event_id"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("MinerU 解析任务返回无效结果") from exc
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
                    try:
                        result = json.loads(text[5:].strip())
                    except ValueError as exc:
                        raise RuntimeError("MinerU 解析事件返回无效结果") from exc
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
        self._lock = threading.RLock()
        recover_expired(store.engine)
        self.recover_index_intents()

    def _repair_intent(self, intent: IndexIntent, milvus: MilvusChunkStore | None = None) -> None:
        job = self.get(intent.job_id)
        if job is None:
            raise RuntimeError("索引意图缺少关联任务，请人工核查")
        with Session(self.store.engine) as session:
            newer_committed = session.scalars(select(DocumentImportRecord.id).where(
                DocumentImportRecord.knowledge_base_id == intent.knowledge_base_id,
                DocumentImportRecord.file_hash == job.file_hash,
                DocumentImportRecord.id != job.id,
                DocumentImportRecord.status == "completed",
                DocumentImportRecord.created_at >= job.created_at,
            )).first()
        if newer_committed:
            intent.clear(self.settings.data_dir)
            return  # A newer successful retry/reimport owns these vectors and cached chunks.
        if not intent.already_indexed:
            index = milvus or MilvusChunkStore(self.settings.milvus_uri, intent.collection, self.settings.milvus_vector_dim)
            if index.has_collection():
                index.delete_document(intent.knowledge_base_id, intent.document_id)
            IngestionPipeline.remove_cached_chunks(self.settings.data_dir / "normalized" / intent.document_id, intent.knowledge_base_id, intent.document_id)
        intent.clear(self.settings.data_dir)

    def recover_index_intents(self) -> None:
        """Reconcile crashed index writes after their document lease expires."""
        try:
            intents = pending_intents(self.settings.data_dir)
        except Exception:
            logger.exception("Cannot read index intents; manual inspection required")
            return
        for intent in intents:
            lease = None
            try:
                lease = try_acquire(self.store.engine, DOCUMENT_LEASE_SCOPE, intent.document_id)
                if lease is None:
                    continue  # Another worker is still handling this document.
                job = self.get(intent.job_id)
                if job is None:
                    logger.error("Index intent %s has no matching job; manual inspection required", intent.job_id)
                    continue
                if job.status in {"queued", "parsing", "embedding"}:
                    self.update(job.id, status="failed", error="索引提交中断，正在清理；可稍后重试")
                    job = self.get(job.id)
                    if job is None:
                        logger.error("Index intent %s lost its job; manual inspection required", intent.job_id)
                        continue
                if job.status == "completed":
                    intent.clear(self.settings.data_dir)
                elif job.status in {"failed", "discarded", "deleted"}:
                    self._repair_intent(intent)
                    if job.status == "failed" and job.error.startswith("索引清理未完成"):
                        self.update(job.id, error="索引清理已完成，可重试")
            except Exception:
                logger.exception("Failed to reconcile index intent %s", intent.job_id)
                try:
                    job = self.get(intent.job_id)
                    if job is not None and job.status == "failed":
                        self.update(job.id, error="索引清理未完成；请检查 Milvus 服务后刷新任务列表。清理完成前不能重试。")
                except Exception:
                    logger.exception("Failed to record index reconciliation error")
            finally:
                if lease is not None:
                    lease.close()

    def get(self, job_id: str, recover: bool = False) -> ImportJob | None:
        if recover:
            recover_expired(self.store.engine)
            self.recover_index_intents()
        with Session(self.store.engine) as session:
            record = session.get(DocumentImportRecord, job_id)
            return to_job(record, self.settings.data_dir) if record else None

    def list(self) -> list[ImportJob]:
        recover_expired(self.store.engine)
        self.recover_index_intents()
        with Session(self.store.engine) as session:
            return [to_job(item, self.settings.data_dir) for item in session.scalars(select(DocumentImportRecord).order_by(DocumentImportRecord.created_at.desc()).limit(50))]

    def update(self, job_id: str, **values: object) -> None:
        with Session(self.store.engine) as session, session.begin():
            session.execute(update(DocumentImportRecord).where(DocumentImportRecord.id == job_id).values(**values, updated_at=now()))

    def start(self, path: Path, file_name: str, knowledge_base_id: str, ocr_mode: str, duplicate_mode: str) -> ImportJob:
        with knowledge_base_gate(self.store.engine, knowledge_base_id):
            return self._start(path, file_name, knowledge_base_id, ocr_mode, duplicate_mode)

    def _start(self, path: Path, file_name: str, knowledge_base_id: str, ocr_mode: str, duplicate_mode: str) -> ImportJob:
        with self._lock:
            self.recover_index_intents()
            digest = sha256_file(path)
            document_id = stable_id("document", digest)
            if any(intent.document_id == document_id for intent in pending_intents(self.settings.data_dir)):
                raise ValueError("该文件存在未完成的索引清理，请先修复后重试")
            job_id = str(uuid4())
            lease = try_acquire(self.store.engine, DOCUMENT_LEASE_SCOPE, stable_id("document", digest), job_id)
            with Session(self.store.engine) as session:
                previous = session.scalars(select(DocumentImportRecord).where(
                    DocumentImportRecord.knowledge_base_id == knowledge_base_id,
                    DocumentImportRecord.file_hash == digest,
                    DocumentImportRecord.status.in_(["queued", "parsing", "embedding", "completed"]),
                ).order_by(DocumentImportRecord.created_at.desc())).first()
            if lease is None:
                if previous and previous.status in {"queued", "parsing", "embedding"} and duplicate_mode == "skip":
                    path.unlink(missing_ok=True)
                    path.parent.rmdir()
                    return to_job(previous, self.settings.data_dir)
                raise ValueError("文档正在由其他任务处理，请稍后重试")
            if previous and (previous.status in {"queued", "parsing", "embedding"} or duplicate_mode == "skip"):
                lease.close()
                if previous.status in {"queued", "parsing", "embedding"} and duplicate_mode == "replace":
                    raise ValueError("相同文件正在处理，请等待任务完成")
                path.unlink(missing_ok=True)
                path.parent.rmdir()
                return to_job(previous, self.settings.data_dir)
            destination = self.settings.data_dir / "uploads" / job_id
            try:
                destination.mkdir(parents=True, exist_ok=False)
            except Exception:
                lease.close()
                raise
            retained = destination / file_name
            try:
                path.replace(retained)
                (destination / "options.json").write_text(json.dumps({"ocr_mode": ocr_mode}))
                if path.parent != destination:
                    path.parent.rmdir()
                stamp = now()
                with Session(self.store.engine) as session, session.begin():
                    record = DocumentImportRecord(id=job_id, file_name=file_name, knowledge_base_id=knowledge_base_id,
                                                  file_hash=digest, status="queued", completed_chunks=0, total_chunks=0,
                                                  error="", created_at=stamp, updated_at=stamp)
                    session.add(record)
                    result = to_job(record, self.settings.data_dir)
            except Exception:
                if retained.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    retained.replace(path)
                (destination / "options.json").unlink(missing_ok=True)
                destination.rmdir()
                lease.close()
                raise
            thread = threading.Thread(target=self._run, args=(job_id, retained, ocr_mode, lease), daemon=True, name=f"document-import-{job_id[:8]}")
            try:
                thread.start()
            except Exception:
                self.update(job_id, status="failed", error="未能启动后台任务，请重试")
                lease.close()
                raise
            return result

    def retry(self, job_id: str) -> ImportJob:
        job = self.get(job_id)
        if job is None:
            raise KeyError("导入任务不存在")
        with knowledge_base_gate(self.store.engine, job.knowledge_base_id):
            return self._retry(job_id)

    def _retry(self, job_id: str) -> ImportJob:
        with self._lock:
            recover_expired(self.store.engine)
            self.recover_index_intents()
            if (self.settings.data_dir / "index-intents" / f"{job_id}.json").exists():
                raise ValueError("该任务索引清理未完成，请检查 Milvus 后重试")
            job = self.get(job_id)
            if job is None:
                raise KeyError("导入任务不存在")
            if job.status != "failed":
                raise ValueError("仅失败的任务可以重试")
            path = retained_source(self.settings.data_dir, job.id, job.file_name)
            if path is None or not path.is_file() or sha256_file(path) != job.file_hash:
                raise ValueError("原文件未保留或内容已变化，请重新选择文件上传")
            options_path = path.parent / "options.json"
            try:
                options = json.loads(options_path.read_text()) if options_path.is_file() else {}
            except (OSError, ValueError) as exc:
                raise ValueError("无法读取解析设置，请重新上传") from exc
            ocr_mode = options.get("ocr_mode", "auto")
            if ocr_mode not in {"auto", "force", "native"}:
                raise ValueError("解析设置无效，请重新上传")
            lease = try_acquire(self.store.engine, DOCUMENT_LEASE_SCOPE, stable_id("document", job.file_hash), job_id)
            if lease is None:
                raise ValueError("文档正在由其他任务处理，请稍后重试")
            handed_off = False
            try:
                with Session(self.store.engine) as session:
                    concurrent = session.scalars(select(DocumentImportRecord).where(
                        DocumentImportRecord.knowledge_base_id == job.knowledge_base_id,
                        DocumentImportRecord.file_hash == job.file_hash,
                        DocumentImportRecord.status.in_(["queued", "parsing", "embedding"]),
                    )).first()
                    current = session.get(DocumentImportRecord, job_id)
                    if current is None or current.status != "failed" or concurrent:
                        raise ValueError("相同文件正在处理或当前任务不可重试")
                self.update(job_id, status="queued", completed_chunks=0, total_chunks=0, error="")
                thread = threading.Thread(target=self._run, args=(job_id, path, ocr_mode, lease), daemon=True, name=f"document-import-{job_id[:8]}")
                try:
                    thread.start()
                    handed_off = True
                except Exception:
                    self.update(job_id, status="failed", error="未能启动后台任务，请重试")
                    raise
                return self.get(job_id)  # type: ignore[return-value]
            except Exception:
                if not handed_off:
                    lease.close()
                raise

    def discard_failed_upload(self, job_id: str) -> ImportJob:
        """Delete only the retained source of a failed import, never its Milvus data."""
        with self._lock:
            recover_expired(self.store.engine)
            job = self.get(job_id)
            if job is None:
                raise KeyError("导入任务不存在")
            if job.status != "failed":
                raise ValueError("仅失败的任务可清理上传原件")
            lease = try_acquire(self.store.engine, DOCUMENT_LEASE_SCOPE, stable_id("document", job.file_hash))
            if lease is None:
                raise ValueError("文档正在由其他任务处理，请稍后重试")
            try:
                current = self.get(job_id)
                if current is None or current.status != "failed":
                    raise ValueError("任务状态已变化，请刷新后重试")
                source = retained_source(self.settings.data_dir, job_id, current.file_name)
                if source is None or not source.is_file():
                    raise ValueError("该任务没有可清理的原文件")
                options = source.parent / "options.json"
                if any(item not in {source, options} for item in source.parent.iterdir()):
                    raise ValueError("上传目录包含未知文件，已拒绝清理")
                lease.ensure_owned()
                source.unlink()
                options.unlink(missing_ok=True)
                source.parent.rmdir()
                self.update(job_id, status="discarded")
                return self.get(job_id)  # type: ignore[return-value]
            finally:
                lease.close()

    def _milvus(self) -> MilvusChunkStore:
        collection = self.store.get_setting("active_collection_name", self.settings.milvus_collection)
        dim = int(self.store.get_setting("active_vector_dim", str(self.settings.milvus_vector_dim)))
        return MilvusChunkStore(self.settings.milvus_uri, collection, dim)

    def documents(self, knowledge_base_id: str) -> list[dict[str, object]]:
        rows = self._milvus().document_chunks(knowledge_base_id)
        documents: dict[str, _DocumentSummary] = {}
        for row in rows:
            doc_id = str(row["document_id"])
            entry = documents.setdefault(doc_id, _DocumentSummary(doc_id, str(row.get("document_name") or "未知文档")))
            entry.chunk_count += 1
            entry.page_count = max(entry.page_count, int(row.get("page_end") or 0))
        return [asdict(entry) for entry in sorted(documents.values(), key=lambda item: item.document_name)]

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
        try:
            chunks = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError("无法读取文档切块，请检查缓存后重试") from exc
        return path, [item for item in chunks if item.get("knowledge_base_id") == knowledge_base_id and item.get("document_id") == document_id]

    def delete_document(self, knowledge_base_id: str, document_id: str) -> bool:
        with self._lock:
            lease = try_acquire(self.store.engine, DOCUMENT_LEASE_SCOPE, document_id)
            if lease is None:
                raise ValueError("文档正在由其他任务处理，请稍后重试")
            try:
                return self._delete_document_locked(knowledge_base_id, document_id, lease)
            finally:
                lease.close()

    def _delete_document_locked(self, knowledge_base_id: str, document_id: str, lease: DocumentLease) -> bool:
        if self.document(knowledge_base_id, document_id, include_content=False) is None:
            return False
        digest_jobs = self._jobs_for_document(knowledge_base_id, document_id)
        if any(item.status in {"queued", "parsing", "embedding"} for item in digest_jobs):
            raise ValueError("文档正在导入，请完成后再删除")
        path, _ = self._cached_chunks(knowledge_base_id, document_id)
        lease.ensure_owned()
        self._milvus().delete_document(knowledge_base_id, document_id)
        IngestionPipeline.remove_cached_chunks(path.parent, knowledge_base_id, document_id)
        for item in digest_jobs:
            if item.status == "completed":
                self.update(item.id, status="deleted")
        return True

    def _jobs_for_document(self, knowledge_base_id: str, document_id: str) -> list[ImportJob]:
        with Session(self.store.engine) as session:
            records = session.scalars(select(DocumentImportRecord).where(DocumentImportRecord.knowledge_base_id == knowledge_base_id)).all()
            return [to_job(record, self.settings.data_dir) for record in records if stable_id("document", record.file_hash) == document_id]

    def reindex_document(self, knowledge_base_id: str, document_id: str) -> ImportJob:
        with knowledge_base_gate(self.store.engine, knowledge_base_id):
            return self._reindex_document(knowledge_base_id, document_id)

    def _reindex_document(self, knowledge_base_id: str, document_id: str) -> ImportJob:
        with self._lock:
            lease = try_acquire(self.store.engine, DOCUMENT_LEASE_SCOPE, document_id)
            if lease is None:
                raise ValueError("文档正在由其他任务处理，请稍后重试")
            try:
                return self._reindex_document_locked(knowledge_base_id, document_id, lease)
            except Exception:
                if not lease.handed_off:
                    lease.close()
                raise

    def _reindex_document_locked(self, knowledge_base_id: str, document_id: str, lease: DocumentLease) -> ImportJob:
        document = self.document(knowledge_base_id, document_id, include_content=False)
        if document is None:
            raise KeyError("文档不存在")
        _, chunks = self._cached_chunks(knowledge_base_id, document_id)
        if not chunks:
            raise ValueError("未保留可重新向量化的切块，请重新上传原文件")
        if any(item.status in {"queued", "parsing", "embedding"} for item in self._jobs_for_document(knowledge_base_id, document_id)):
            raise ValueError("文档已有进行中的任务")
        metadata = self.settings.data_dir / "normalized" / document_id / "document.json"
        try:
            file_hash = json.loads(metadata.read_text()).get("file_hash", "") if metadata.is_file() else ""
        except (OSError, ValueError) as exc:
            raise ValueError("无法读取文档标识，请检查缓存后重试") from exc
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
        lease.attach_job(job_id)
        thread = threading.Thread(target=self._run_reindex, args=(job_id, document_id, lease), daemon=True, name=f"document-reindex-{job_id[:8]}")
        try:
            thread.start()
        except Exception:
            self.update(job_id, status="failed", error="未能启动后台任务，请重试")
            raise
        lease.handed_off = True
        return self.get(job_id)  # type: ignore[return-value]

    def _run_reindex(self, job_id: str, document_id: str, lease: DocumentLease | None = None) -> None:
        update_job = lease.update_job if lease is not None else self.update
        job = self.get(job_id)
        if job is None:
            if lease is not None:
                lease.close()
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
            update_job(job_id, status="embedding")
            batch_size = max(1, int(profile.options.get("batch_size", 8)))
            embedded = []
            for offset in range(0, len(chunks), batch_size):
                if self.store.get_setting("active_collection_name", self.settings.milvus_collection) != collection:
                    raise RuntimeError("Embedding 索引已切换，请重新尝试")
                batch = chunks[offset:offset + batch_size]
                vectors = embedder.embed([str(item["content"]) for item in batch])
                embedded.extend(Chunk(**{**item, "block_types": tuple(item.get("block_types") or ()), "vector": None}).with_vector(vector) for item, vector in zip(batch, vectors, strict=True))
                update_job(job_id, completed_chunks=len(embedded))
            if self.store.get_setting("active_collection_name", self.settings.milvus_collection) != collection:
                raise RuntimeError("Embedding 索引已切换，请重新尝试")
            if lease is not None:
                lease.ensure_owned()
            milvus.upsert(embedded)
            if lease is not None:
                lease.ensure_owned()
            update_job(job_id, status="completed")
        except Exception as exc:
            try:
                update_job(job_id, status="failed", error=str(exc)[:1000])
            except RuntimeError:
                logger.warning("Skipping failure update for lost document lease: %s", job_id)
        finally:
            if lease is not None:
                lease.close()

    def _run(self, job_id: str, path: Path, ocr_mode: str, lease: DocumentLease | None = None) -> None:
        update_job = lease.update_job if lease is not None else self.update
        job = self.get(job_id)
        if job is None:
            if lease is not None:
                lease.close()
            return
        milvus = None
        prepared = None
        intent = None
        already_indexed = True
        try:
            profile = self.store.get_profile(self.store.get_setting("active_embedding_profile_id"))
            if profile is None or not profile.enabled:
                raise RuntimeError("尚未启用 Embedding 配置")
            dim = int(self.store.get_setting("active_vector_dim", str(profile.vector_dim or 0)))
            if dim <= 0:
                raise RuntimeError("Embedding 向量维度未设置")
            collection = self.store.get_setting("active_collection_name", self.settings.milvus_collection)
            update_job(job_id, status="parsing")
            mineru_list = None
            if path.suffix.lower() == ".pdf" and ocr_mode != "native":
                mineru_list = parse_mineru(path, self.settings.data_dir / "mineru" / job_id, self.settings.mineru_base_url, ocr_mode == "force")
            pipeline = IngestionPipeline(self.settings)
            prepared = pipeline.prepare(path, job.knowledge_base_id, mineru_list, persist=False)
            if not prepared.chunks:
                raise RuntimeError("文档未提取出可索引内容")
            update_job(job_id, status="embedding", total_chunks=len(prepared.chunks))
            embedder = create_embedder(profile)
            milvus = MilvusChunkStore(self.settings.milvus_uri, collection, dim)
            already_indexed = bool(milvus.document_chunks(job.knowledge_base_id, prepared.document.document_id))
            batch_size = max(1, int(profile.options.get("batch_size", 8)))
            embedded = []
            for offset in range(0, len(prepared.chunks), batch_size):
                if self.store.get_setting("active_collection_name", self.settings.milvus_collection) != collection:
                    raise RuntimeError("Embedding 索引已切换，请重新导入")
                batch = prepared.chunks[offset:offset + batch_size]
                vectors = embedder.embed([chunk.content for chunk in batch])
                embedded.extend(chunk.with_vector(vector) for chunk, vector in zip(batch, vectors, strict=True))
                update_job(job_id, completed_chunks=len(embedded))
            if self.store.get_setting("active_collection_name", self.settings.milvus_collection) != collection:
                raise RuntimeError("Embedding 索引已切换，请重新导入")
            if lease is not None:
                lease.ensure_owned()
            intent = IndexIntent(job_id, job.knowledge_base_id, prepared.document.document_id, collection, already_indexed)
            intent.persist(self.settings.data_dir)
            milvus.upsert(embedded)
            if lease is not None:
                lease.ensure_owned()
            pipeline.save_prepared(prepared)
            update_job(job_id, status="completed")
            try:
                intent.clear(self.settings.data_dir)
            except OSError:
                logger.exception("Committed import %s retained index intent for later cleanup", job_id)
        except Exception as exc:
            error = str(exc)
            can_clean = True
            if lease is not None:
                try:
                    lease.ensure_owned()
                except RuntimeError:
                    can_clean = False
            marked_failed = False
            try:
                update_job(job_id, status="failed", error=error[:1000])
                marked_failed = True
            except RuntimeError:
                logger.warning("Skipping failure update for lost document lease: %s", job_id)
            if intent is not None and can_clean and marked_failed:
                try:
                    self._repair_intent(intent, milvus)
                except Exception as cleanup_error:
                    logger.exception("Failed to roll back incomplete document index")
                    try:
                        update_job(job_id, error=f"{error}；未能清理不完整索引：{cleanup_error}"[:1000])
                    except RuntimeError:
                        logger.warning("Could not record index cleanup failure for %s", job_id)
        finally:
            try:
                still_owned = True
                if lease is not None:
                    try:
                        lease.ensure_owned()
                    except RuntimeError:
                        still_owned = False
                if still_owned and self.get(job_id).status == "completed":  # type: ignore[union-attr]
                    path.unlink(missing_ok=True)
                    (path.parent / "options.json").unlink(missing_ok=True)
                    path.parent.rmdir()
            finally:
                if lease is not None:
                    lease.close()

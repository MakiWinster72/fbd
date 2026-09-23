from __future__ import annotations

from knowledge_rag.documents import sha256_file
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

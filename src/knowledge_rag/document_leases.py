"""Database-backed document leases shared by all API workers.

A short transaction claims a document; a heartbeat holds it across long parses.
Expired owners are fenced out before indexing, and may be replaced after a crash.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, exists, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from knowledge_rag.db import DocumentImportRecord, DocumentLeaseRecord

logger = logging.getLogger(__name__)
LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 15


def _time(offset: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset)).isoformat()


class DocumentLease:
    def __init__(self, engine: Engine, knowledge_base_id: str, document_id: str, owner: str) -> None:
        self.engine = engine
        self.knowledge_base_id = knowledge_base_id
        self.document_id = document_id
        self.owner = owner
        self.handed_off = False
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._heartbeat = threading.Thread(target=self._renew, daemon=True, name=f"document-lease-{owner[:8]}")
        self._heartbeat.start()

    def _renew(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                with Session(self.engine) as session, session.begin():
                    changed = session.execute(update(DocumentLeaseRecord).where(
                        DocumentLeaseRecord.knowledge_base_id == self.knowledge_base_id,
                        DocumentLeaseRecord.document_id == self.document_id,
                        DocumentLeaseRecord.owner == self.owner,
                        DocumentLeaseRecord.expires_at > _time(),
                    ).values(expires_at=_time(LEASE_SECONDS))).rowcount
                if changed != 1:
                    self._lost.set()
                    return
            except Exception:
                logger.exception("Document lease heartbeat failed")
                # A transient DB failure can be retried; ensure_owned rejects an expired lease.

    def attach_job(self, job_id: str) -> None:
        with Session(self.engine) as session, session.begin():
            changed = session.execute(update(DocumentLeaseRecord).where(
                DocumentLeaseRecord.knowledge_base_id == self.knowledge_base_id,
                DocumentLeaseRecord.document_id == self.document_id,
                DocumentLeaseRecord.owner == self.owner,
                DocumentLeaseRecord.expires_at > _time(),
            ).values(job_id=job_id)).rowcount
            if changed != 1:
                raise RuntimeError("文档任务租约已失效，请重试")

    def ensure_owned(self) -> None:
        if self._lost.is_set():
            raise RuntimeError("文档任务租约已失效，请重试")
        with Session(self.engine) as session:
            record = session.get(DocumentLeaseRecord, (self.knowledge_base_id, self.document_id))
            if record is None or record.owner != self.owner or record.expires_at <= _time():
                self._lost.set()
                raise RuntimeError("文档任务租约已失效，请重试")

    def update_job(self, job_id: str, **values: object) -> None:
        self.ensure_owned()
        with Session(self.engine) as session, session.begin():
            owned = exists(select(DocumentLeaseRecord.owner).where(
                DocumentLeaseRecord.knowledge_base_id == self.knowledge_base_id,
                DocumentLeaseRecord.document_id == self.document_id,
                DocumentLeaseRecord.owner == self.owner,
                DocumentLeaseRecord.job_id == job_id,
                DocumentLeaseRecord.expires_at > _time(),
            ))
            changed = session.execute(update(DocumentImportRecord).where(
                DocumentImportRecord.id == job_id, owned,
            ).values(**values, updated_at=_time())).rowcount
            if changed != 1:
                self._lost.set()
                raise RuntimeError("文档任务租约已失效，请重试")

    def close(self) -> None:
        self._stop.set()
        if threading.current_thread() is not self._heartbeat:
            self._heartbeat.join(timeout=2)
        with Session(self.engine) as session, session.begin():
            session.execute(delete(DocumentLeaseRecord).where(
                DocumentLeaseRecord.knowledge_base_id == self.knowledge_base_id,
                DocumentLeaseRecord.document_id == self.document_id,
                DocumentLeaseRecord.owner == self.owner,
            ))


def try_acquire(engine: Engine, knowledge_base_id: str, document_id: str, job_id: str | None = None) -> DocumentLease | None:
    owner = str(uuid4())
    expiry = _time(LEASE_SECONDS)
    try:
        with Session(engine) as session, session.begin():
            session.add(DocumentLeaseRecord(knowledge_base_id=knowledge_base_id, document_id=document_id,
                                            owner=owner, job_id=job_id, expires_at=expiry))
            session.flush()
    except IntegrityError:
        with Session(engine) as session, session.begin():
            previous = session.get(DocumentLeaseRecord, (knowledge_base_id, document_id))
            if previous is None:
                return None  # Concurrent release: retry on the next request.
            stale_job_id = previous.job_id
            changed = session.execute(update(DocumentLeaseRecord).where(
                DocumentLeaseRecord.knowledge_base_id == knowledge_base_id,
                DocumentLeaseRecord.document_id == document_id,
                DocumentLeaseRecord.expires_at <= _time(),
            ).values(owner=owner, job_id=job_id, expires_at=expiry)).rowcount
            if changed != 1:
                return None
            if stale_job_id:
                session.execute(update(DocumentImportRecord).where(
                    DocumentImportRecord.id == stale_job_id,
                    DocumentImportRecord.status.in_(["queued", "parsing", "embedding"]),
                ).values(status="failed", error="任务租约过期，请重试", updated_at=_time()))
    return DocumentLease(engine, knowledge_base_id, document_id, owner)


def recover_expired(engine: Engine) -> None:
    """Release expired owners, never invalidate tasks still owned by another worker."""
    with Session(engine) as session:
        keys = [(item.knowledge_base_id, item.document_id) for item in session.scalars(
            select(DocumentLeaseRecord).where(DocumentLeaseRecord.expires_at <= _time())
        )]
    for knowledge_base_id, document_id in keys:
        lease = try_acquire(engine, knowledge_base_id, document_id)
        if lease is not None:
            lease.close()

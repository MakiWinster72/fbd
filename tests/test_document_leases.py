from __future__ import annotations

import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.orm import Session

from knowledge_rag.admin_store import AdminStore
from knowledge_rag.db import DocumentLeaseRecord
from knowledge_rag.document_leases import try_acquire


def test_heartbeat_renews_shared_lease_and_release_allows_reclaim() -> None:
    with tempfile.TemporaryDirectory() as directory:
        first = AdminStore(Path(directory) / "shared.db", "test-secret")
        second = AdminStore(Path(directory) / "shared.db", "test-secret")
        key = ("__shared_document__", "a" * 64)
        with patch("knowledge_rag.document_leases.HEARTBEAT_SECONDS", 0.02), patch("knowledge_rag.document_leases.LEASE_SECONDS", 1):
            lease = try_acquire(first.engine, *key, job_id="test-job")
            assert lease is not None
            try:
                with Session(second.engine) as session:
                    initial = session.get(DocumentLeaseRecord, key).expires_at
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    with Session(second.engine) as session:
                        if session.get(DocumentLeaseRecord, key).expires_at > initial:
                            break
                    time.sleep(0.01)
                else:
                    raise AssertionError("lease heartbeat did not renew")
                assert try_acquire(second.engine, *key) is None
                lease.ensure_owned()
            finally:
                lease.close()
            claimed = try_acquire(second.engine, *key)
            assert claimed is not None
            claimed.close()
        second.engine.dispose()
        first.engine.dispose()

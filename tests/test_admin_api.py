from __future__ import annotations

import hashlib
import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from knowledge_rag.admin_store import AdminStore
from knowledge_rag.api import create_app
from knowledge_rag.config import Settings
from knowledge_rag.db import DocumentLeaseRecord, UserKnowledgeBaseRecord
from knowledge_rag.document_import import DOCUMENT_LEASE_SCOPE
from knowledge_rag.documents import stable_id
from knowledge_rag.rag import ChatResult, RagService


class FakeRagService:
    def retrieve(self, question: str, knowledge_base_id: str):
        return ()

    def answer(self, question: str, knowledge_base_id: str, locale: str = "zh-CN"):
        return ChatResult("当前知识库中没有足够依据回答该问题。", False, ())


class AdminApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        settings = replace(
            Settings(),
            app_db_path=Path(self.temp_dir.name) / "app.db",
            data_dir=Path(self.temp_dir.name) / "data",
            model_secret_key="test-only-secret",
            auth_required=True,
        )
        self.store = AdminStore(settings.app_db_path, settings.model_secret_key)
        self.store.seed_defaults(settings)
        self.admin = self.store.create_user("admin", "管理员", "strong-password", "admin")
        self.user = self.store.create_user("reader", "普通用户", "reader-password", "user")
        self.store.set_user_knowledge_bases(self.user.id, ["default"], self.admin.id)
        self.app = create_app(rag_service=Mock(spec=RagService, wraps=FakeRagService()), settings=settings, admin_store=self.store)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def login(self, username: str, password: str) -> None:
        response = self.client.post("/api/v1/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)

    def wait_for_no_lease(self, _kb: str, doc_id: str) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with Session(self.store.engine) as session:
                if session.get(DocumentLeaseRecord, (DOCUMENT_LEASE_SCOPE, doc_id)) is None:
                    return
            time.sleep(0.01)
        self.fail("后台任务未释放文档租约")

    def test_delete_knowledge_base_requires_confirmation_and_retains_history(self) -> None:
        from sqlalchemy import select

        from knowledge_rag.conversation_store import ConversationStore
        from knowledge_rag.db import AuditLogRecord, KnowledgeBaseRecord

        base = self.store.create_knowledge_base("delete-me", "待删除", "", self.admin.id)
        self.store.set_user_knowledge_bases(self.user.id, [base.id], self.admin.id)
        history = ConversationStore(self.store.engine)
        conversation = history.create(self.user.id, base.id, "保留的历史")
        endpoint = f"/api/v1/admin/knowledge-bases/{base.id}"
        def request(text):
            return self.client.request("DELETE", endpoint, json={"confirmation": text})
        self.assertEqual(request("我确认删除(待删除)").status_code, 401)
        self.login("reader", "reader-password")
        self.assertEqual(request("我确认删除(待删除)").status_code, 403)
        self.login("admin", "strong-password")
        self.assertEqual(request("错误文字").status_code, 409)
        self.assertTrue(self.store.user_can_access(self.user, base.id))
        vector_store = Mock()
        vector_store.collection = self.app.state.settings.milvus_collection
        vector_store.vector_dim = 1024
        vector_store.document_chunks.return_value = []
        with patch("knowledge_rag.document_import.DocumentImportManager._milvus", return_value=vector_store):
            response = request("我确认删除(待删除)")
        self.assertEqual(response.status_code, 204, response.text)
        self.assertFalse(self.store.user_can_access(self.user, base.id))
        self.assertEqual(self.store.user_knowledge_base_ids(self.user.id), [])
        self.assertEqual(history.get(self.user.id, conversation.id)[0].id, conversation.id)
        with Session(self.store.engine) as session:
            self.assertIsNotNone(session.get(KnowledgeBaseRecord, base.id))
            self.assertIsNotNone(session.scalar(select(AuditLogRecord).where(AuditLogRecord.action == "knowledge_base.delete", AuditLogRecord.target_id == base.id)))
        with self.assertRaises(PermissionError):
            history.create(self.user.id, base.id, "不能继续")
        original_grants = self.store.user_knowledge_base_ids(self.user.id)
        with self.assertRaises(ValueError):
            self.store.set_user_knowledge_bases(self.user.id, ["default", base.id], self.admin.id)
        self.assertEqual(self.store.user_knowledge_base_ids(self.user.id), original_grants)
        with Session(self.store.engine) as session:
            self.assertIsNone(session.get(UserKnowledgeBaseRecord, (self.user.id, base.id)))
            self.assertIsNone(session.get(UserKnowledgeBaseRecord, (self.user.id, "default")))

    def test_delete_cleanup_failure_is_retryable_and_preserves_shared_cache(self) -> None:
        from knowledge_rag.documents import stable_id

        self.login("admin", "strong-password")
        base = self.store.create_knowledge_base("cleanup-qa", "清理验收", "", self.admin.id)
        with patch("knowledge_rag.document_import.DocumentImportManager._run", side_effect=lambda job, path, mode, lease: lease.close()):
            response = self.client.post("/api/v1/admin/document-imports", data={"knowledge_base_id": base.id, "ocr_mode": "native"}, files={"file": ("cleanup.pdf", b"cleanup-qa-only")})
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["id"]
        document_id = stable_id("document", response.json()["file_hash"])
        self.wait_for_no_lease(base.id, document_id)
        manager = self.app.state.document_import_manager
        manager.update(job_id, status="failed")
        source = self.app.state.settings.data_dir / "uploads" / job_id / "cleanup.pdf"
        normalized = self.app.state.settings.data_dir / "normalized" / document_id
        normalized.mkdir(parents=True)
        chunks = [{"knowledge_base_id": base.id, "document_id": document_id, "content": "目标资料"}, {"knowledge_base_id": "default", "document_id": document_id, "content": "其他库保留"}]
        (normalized / "chunks.json").write_text(json.dumps(chunks))
        (normalized / "chunks-with-vectors.json").write_text(json.dumps(chunks))
        vector_store = Mock()
        vector_store.collection = self.app.state.settings.milvus_collection
        vector_store.vector_dim = 1024
        vector_store.document_chunks.return_value = [{"document_id": document_id}]
        vector_store.has_collection.return_value = True
        vector_store.delete_document.side_effect = RuntimeError("simulated vector service outage")
        endpoint = f"/api/v1/admin/knowledge-bases/{base.id}"
        confirmation = {"confirmation": "我确认删除(清理验收)"}
        with patch.object(manager, "_milvus", return_value=vector_store):
            failed = self.client.request("DELETE", endpoint, json=confirmation)
            self.assertEqual(failed.status_code, 503, failed.text)
            self.assertTrue(source.exists())
            self.assertFalse(self.store.user_can_access(self.admin, base.id))
            pending = self.client.get("/api/v1/admin/knowledge-bases?include_pending_deletion=true").json()
            self.assertIn(base.id, [item["id"] for item in pending])
            original_grants = self.store.user_knowledge_base_ids(self.user.id)
            with self.assertRaises(ValueError):
                self.store.set_user_knowledge_bases(self.user.id, ["default", base.id], self.admin.id)
            self.assertEqual(self.store.user_knowledge_base_ids(self.user.id), original_grants)
            with Session(self.store.engine) as session:
                self.assertIsNone(session.get(UserKnowledgeBaseRecord, (self.user.id, base.id)))
                self.assertIsNotNone(session.get(UserKnowledgeBaseRecord, (self.user.id, "default")))
            vector_store.delete_document.side_effect = None
            deleted = self.client.request("DELETE", endpoint, json=confirmation)
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertFalse(source.exists())
        self.assertEqual(json.loads((normalized / "chunks.json").read_text()), [chunks[1]])
        self.assertEqual(json.loads((normalized / "chunks-with-vectors.json").read_text()), [chunks[1]])
        self.assertEqual(manager.get(job_id).status, "deleted")
        pending = self.client.get("/api/v1/admin/knowledge-bases?include_pending_deletion=true").json()
        self.assertNotIn(base.id, [item["id"] for item in pending])

    def test_create_knowledge_base(self) -> None:
        endpoint = "/api/v1/admin/knowledge-bases"
        payload = {"id": "research-2026", "name": "  科研资料  ", "description": "研究材料"}
        self.assertEqual(self.client.post(endpoint, json=payload).status_code, 401)
        self.login("reader", "reader-password")
        self.assertEqual(self.client.post(endpoint, json=payload).status_code, 403)
        self.login("admin", "strong-password")
        response = self.client.post(endpoint, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["name"], "科研资料")
        self.assertTrue(response.json()["active"])
        self.assertEqual(self.client.post(endpoint, json=payload).status_code, 409)
        for invalid in [{**payload, "id": "../bad"}, {**payload, "name": "   "}, {**payload, "id": ""}]:
            self.assertIn(self.client.post(endpoint, json=invalid).status_code, (400, 422))
        self.login("reader", "reader-password")
        self.assertNotIn("research-2026", [item["id"] for item in self.client.get("/api/v1/knowledge-bases").json()])
        self.store.set_user_knowledge_bases(self.user.id, ["default", "research-2026"], self.admin.id)
        self.assertIn("research-2026", [item["id"] for item in self.client.get("/api/v1/knowledge-bases").json()])

    def test_document_import_requires_admin_and_validates_upload(self) -> None:
        endpoint = "/api/v1/admin/document-imports"
        payload = {"knowledge_base_id": "default", "ocr_mode": "native"}
        self.assertEqual(self.client.post(endpoint, data=payload, files={"file": ("a.pdf", b"data")}).status_code, 401)
        self.login("reader", "reader-password")
        self.assertEqual(self.client.post(endpoint, data=payload, files={"file": ("a.pdf", b"data")}).status_code, 403)
        self.login("admin", "strong-password")
        self.assertEqual(self.client.post(endpoint, data=payload, files={"file": ("a.doc", b"data")}).status_code, 400)
        self.assertEqual(self.client.post(endpoint, data=payload, files={"file": ("a.pdf", b"")}).status_code, 400)
        with patch("knowledge_rag.document_import.DocumentImportManager._run", side_effect=lambda job, path, mode, lease: lease.close()):
            response = self.client.post(endpoint, data=payload, files={"file": ("a.pdf", b"data")})
            self.assertEqual(response.status_code, 202, response.text)
            job_id = response.json()["id"]
            self.wait_for_no_lease("default", stable_id("document", response.json()["file_hash"]))
            self.assertEqual(self.client.get(f"{endpoint}/{job_id}").json()["status"], "queued")
            duplicate = self.client.post(endpoint, data=payload, files={"file": ("a.pdf", b"data")})
            self.assertEqual(duplicate.status_code, 202)
            self.assertEqual(duplicate.json()["id"], job_id)
            self.assertEqual(len(self.client.get(endpoint).json()), 1)
            from knowledge_rag.document_import import DocumentImportManager
            DocumentImportManager(self.app.state.settings, self.store).update(job_id, status="completed")
            skipped = self.client.post(endpoint, data=payload, files={"file": ("a.pdf", b"data")})
            self.assertEqual(skipped.json()["id"], job_id)
            replaced = self.client.post(endpoint, data={**payload, "duplicate_mode": "replace"}, files={"file": ("a.pdf", b"data")})
            self.assertNotEqual(replaced.json()["id"], job_id)
            self.assertEqual(len(self.client.get(endpoint).json()), 2)

    def test_text_upload_formats_are_accepted_without_ocr(self) -> None:
        self.login("admin", "strong-password")
        endpoint = "/api/v1/admin/document-imports"
        with patch("knowledge_rag.document_import.DocumentImportManager._run", side_effect=lambda job, path, mode, lease: lease.close()):
            for suffix in ("md", "txt", "MD", "TXT"):
                with self.subTest(suffix=suffix):
                    content = f"# 设备\n这是 {suffix} 上传测试。".encode()
                    response = self.client.post(endpoint, data={"knowledge_base_id": "default"}, files={"file": (f"设备.{suffix}", content)})
                    self.assertEqual(response.status_code, 202, response.text)
                    self.wait_for_no_lease("default", stable_id("document", response.json()["file_hash"]))

    def test_failed_import_retains_file_and_retries_original_job(self) -> None:
        endpoint = "/api/v1/admin/document-imports"
        self.login("admin", "strong-password")
        self.assertEqual(self.client.post(f"{endpoint}/missing/retry").status_code, 404)
        with patch("knowledge_rag.document_import.DocumentImportManager._run", side_effect=lambda job, path, mode, lease: lease._stop.set()):
            first = self.client.post(endpoint, data={"knowledge_base_id": "default", "ocr_mode": "native"}, files={"file": ("fixture.pdf", b"original")})
            self.assertEqual(first.status_code, 202, first.text)
            job_id = first.json()["id"]
            path = self.app.state.settings.data_dir / "uploads" / job_id / "fixture.pdf"
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(json.loads((path.parent / "options.json").read_text())["ocr_mode"], "native")
            collision = self.client.post(endpoint, data={"knowledge_base_id": "default", "duplicate_mode": "replace"}, files={"file": ("fixture.pdf", b"original")})
            self.assertEqual(collision.status_code, 409)
            self.assertEqual(path.read_bytes(), b"original")
            manager = self.app.state.document_import_manager
            from knowledge_rag.document_import import DocumentImportManager
            DocumentImportManager(self.app.state.settings, self.store)
            self.assertEqual(manager.get(job_id).status, "queued")  # A live worker must not be invalidated.
            with Session(self.store.engine) as session, session.begin():
                expired = session.get(DocumentLeaseRecord, (DOCUMENT_LEASE_SCOPE, stable_id("document", first.json()["file_hash"])))
                assert expired is not None
                expired.expires_at = "2000-01-01T00:00:00+00:00"
            DocumentImportManager(self.app.state.settings, self.store)
            self.assertEqual(manager.get(job_id).status, "failed")
            self.assertEqual(path.read_bytes(), b"original")
            retry = self.client.post(f"{endpoint}/{job_id}/retry")
            self.assertEqual(retry.status_code, 202, retry.text)
            self.assertEqual(retry.json()["id"], job_id)
            self.assertEqual(retry.json()["status"], "queued")
            self.assertEqual(len(self.client.get(endpoint).json()), 1)
            self.assertEqual(self.client.post(f"{endpoint}/{job_id}/retry").status_code, 409)
            manager.update(job_id, status="failed")
            path.unlink()
            self.assertEqual(self.client.post(f"{endpoint}/{job_id}/retry").status_code, 409)

    def test_independent_managers_fence_competing_operations(self) -> None:
        from knowledge_rag.document_import import DocumentImportManager
        other_store = AdminStore(self.app.state.settings.app_db_path, "test-only-secret")
        first_manager = self.app.state.document_import_manager
        self.store.create_knowledge_base("other", "其他知识库", "", self.admin.id)
        second_manager = DocumentImportManager(self.app.state.settings, other_store)
        staging = self.app.state.settings.data_dir / "uploads" / "staging-first"
        staging.mkdir(parents=True)
        (staging / "same.pdf").write_bytes(b"shared")
        with patch("knowledge_rag.document_import.DocumentImportManager._run") as worker:
            worker.side_effect = lambda job, path, mode, lease: lease._stop.set() if worker.call_count == 1 else lease.close()
            job = first_manager.start(staging / "same.pdf", "same.pdf", "default", "native", "replace")
            deadline = time.monotonic() + 2
            while worker.call_args is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert worker.call_args is not None
            old_lease = worker.call_args.args[-1]
            DocumentImportManager(self.app.state.settings, other_store)
            current_job = second_manager.get(job.id)
            assert current_job is not None
            self.assertEqual(current_job.status, "queued")
            competing = self.app.state.settings.data_dir / "uploads" / "staging-second"
            competing.mkdir(parents=True)
            (competing / "same.pdf").write_bytes(b"shared")
            with self.assertRaisesRegex(ValueError, "正在由其他任务处理"):
                second_manager.start(competing / "same.pdf", "same.pdf", "default", "native", "replace")
            with self.assertRaisesRegex(ValueError, "正在由其他任务处理"):
                second_manager.start(competing / "same.pdf", "same.pdf", "other", "native", "replace")
            with self.assertRaisesRegex(ValueError, "正在由其他任务处理"):
                second_manager.delete_document("default", stable_id("document", job.file_hash))
            with self.assertRaisesRegex(ValueError, "正在由其他任务处理"):
                second_manager.reindex_document("default", stable_id("document", job.file_hash))
            (competing / "same.pdf").unlink()
            competing.rmdir()
            with Session(other_store.engine) as session, session.begin():
                expired = session.get(DocumentLeaseRecord, (DOCUMENT_LEASE_SCOPE, stable_id("document", job.file_hash)))
                assert expired is not None
                expired.expires_at = "2000-01-01T00:00:00+00:00"
            self.assertEqual(next(item.status for item in second_manager.list() if item.id == job.id), "failed")
            self.assertEqual(first_manager.get(job.id).status, "failed")
            with self.assertRaisesRegex(RuntimeError, "租约已失效"):
                old_lease.update_job(job.id, status="completed")
            retry = second_manager.retry(job.id)
            self.assertEqual(retry.id, job.id)
            self.assertEqual(retry.status, "queued")
            with self.assertRaisesRegex(RuntimeError, "租约已失效"):
                old_lease.update_job(job.id, status="failed")
            current_job = second_manager.get(job.id)
            assert current_job is not None
            self.assertEqual(current_job.status, "queued")
            with self.assertRaises(ValueError):
                first_manager.retry(job.id)
        self.wait_for_no_lease("default", stable_id("document", job.file_hash))
        other_store.engine.dispose()

    def test_discard_failed_upload_preserves_record_and_respects_lease(self) -> None:
        from knowledge_rag.document_leases import try_acquire
        endpoint = "/api/v1/admin/document-imports"
        self.assertEqual(self.client.post(f"{endpoint}/missing/discard").status_code, 401)
        self.login("reader", "reader-password")
        self.assertEqual(self.client.post(f"{endpoint}/missing/discard").status_code, 403)
        self.login("admin", "strong-password")
        with patch("knowledge_rag.document_import.DocumentImportManager._run", side_effect=lambda job, path, mode, lease: lease.close()):
            response = self.client.post(endpoint, data={"knowledge_base_id": "default", "ocr_mode": "native"}, files={"file": ("failed.pdf", b"retained")})
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["id"]
        doc_id = stable_id("document", response.json()["file_hash"])
        self.wait_for_no_lease("default", doc_id)
        path = self.app.state.settings.data_dir / "uploads" / job_id / "failed.pdf"
        self.assertEqual(self.client.post(f"{endpoint}/{job_id}/discard").status_code, 409)
        manager = self.app.state.document_import_manager
        manager.update(job_id, status="failed", error="解析失败")
        self.assertTrue(self.client.get(f"{endpoint}/{job_id}").json()["source_available"])
        other_store = AdminStore(self.app.state.settings.app_db_path, "test-only-secret")
        lease = try_acquire(other_store.engine, DOCUMENT_LEASE_SCOPE, doc_id)
        assert lease is not None
        try:
            self.assertEqual(self.client.post(f"{endpoint}/{job_id}/discard").status_code, 409)
            self.assertTrue(path.exists())
        finally:
            lease.close()
            other_store.engine.dispose()
        with patch.object(manager, "_milvus", side_effect=AssertionError("清理原件不应访问索引")):
            cleared = self.client.post(f"{endpoint}/{job_id}/discard")
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()["status"], "discarded")
        self.assertFalse(cleared.json()["source_available"])
        self.assertFalse(path.parent.exists())
        self.assertEqual(self.client.post(f"{endpoint}/{job_id}/retry").status_code, 409)
        self.assertEqual(self.client.post(f"{endpoint}/{job_id}/discard").status_code, 409)
        self.assertEqual(len(self.client.get(endpoint).json()), 1)

    def test_embedding_failure_does_not_write_partial_index_and_retry_cleans_up(self) -> None:
        from knowledge_rag.models import Chunk, ParsedDocument
        from knowledge_rag.pipeline import PreparedIngestion
        self.login("admin", "strong-password")
        with patch("knowledge_rag.document_import.DocumentImportManager._run", side_effect=lambda job, path, mode, lease: lease.close()):
            response = self.client.post("/api/v1/admin/document-imports", data={"knowledge_base_id": "default", "ocr_mode": "native"}, files={"file": ("fixture.pdf", b"original")})
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["id"]
        self.wait_for_no_lease("default", stable_id("document", response.json()["file_hash"]))
        path = self.app.state.settings.data_dir / "uploads" / job_id / "fixture.pdf"
        chunks = tuple(Chunk(str(index), "doc", "logical", "default", "fixture.pdf", 1, 1, "", index, "正文", "hash", ()) for index in range(9))
        document = ParsedDocument("doc", "logical", "fixture.pdf", response.json()["file_hash"], 1, (), ())
        prepared = PreparedIngestion(document, chunks, self.app.state.settings.data_dir / "normalized" / "doc")
        class Embedder:
            def __init__(self):
                self.calls = 0
                self.fail = True
            def embed(self, texts):
                self.calls += 1
                if self.fail and self.calls == 2:
                    raise RuntimeError("embedding interrupted")
                return [[0.1, 0.2] for _ in texts]
        class Milvus:
            def __init__(self):
                self.writes = []
                self.fail_write = False
                self.rollbacks = 0
            def document_chunks(self, kb, doc_id):
                return []
            def has_collection(self):
                return bool(self.writes)
            def upsert(self, chunks):
                self.writes.append(chunks)
                if self.fail_write:
                    raise RuntimeError("Milvus write interrupted")
            def delete_document(self, kb, doc_id):
                self.rollbacks += 1
                self.writes.clear()
        embedder = Embedder()
        milvus = Milvus()
        manager = self.app.state.document_import_manager
        with patch("knowledge_rag.document_import.IngestionPipeline.prepare", return_value=prepared), patch("knowledge_rag.document_import.create_embedder", return_value=embedder), patch("knowledge_rag.document_import.MilvusChunkStore", return_value=milvus):
            manager._run(job_id, path, "native")
            self.assertEqual(manager.get(job_id).status, "failed")
            self.assertEqual(len(milvus.writes), 0)
            self.assertTrue(path.exists())
            embedder.fail = False
            milvus.fail_write = True
            with patch("knowledge_rag.document_import.DocumentImportManager._run", side_effect=lambda job, path, mode, lease: lease.close()):
                retried = self.client.post(f"/api/v1/admin/document-imports/{job_id}/retry")
            self.assertEqual(retried.status_code, 202, retried.text)
            self.wait_for_no_lease("default", stable_id("document", response.json()["file_hash"]))
            manager._run(job_id, path, "native")
            self.assertEqual(manager.get(job_id).status, "failed")
            self.assertEqual(milvus.rollbacks, 1)
            self.assertTrue(path.exists())
            milvus.fail_write = False
            with patch("knowledge_rag.document_import.DocumentImportManager._run", side_effect=lambda job, path, mode, lease: lease.close()):
                self.assertEqual(self.client.post(f"/api/v1/admin/document-imports/{job_id}/retry").status_code, 202)
            self.wait_for_no_lease("default", stable_id("document", response.json()["file_hash"]))
            manager._run(job_id, path, "native")
        self.assertEqual(manager.get(job_id).status, "completed")
        self.assertEqual(len(milvus.writes), 1)
        self.assertEqual(len(milvus.writes[0]), 9)
        self.assertFalse(path.exists())
        self.assertFalse(path.parent.exists())

    def test_interrupted_index_commit_is_repaired_after_restart(self) -> None:
        from knowledge_rag.document_import import DocumentImportManager
        from knowledge_rag.models import Chunk, ParsedDocument
        from knowledge_rag.pipeline import PreparedIngestion
        self.login("admin", "strong-password")
        with patch("knowledge_rag.document_import.DocumentImportManager._run", side_effect=lambda job, path, mode, lease: lease.close()):
            response = self.client.post("/api/v1/admin/document-imports", data={"knowledge_base_id": "default", "ocr_mode": "native"}, files={"file": ("crash.pdf", b"synthetic")})
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["id"]
        doc_id = stable_id("document", response.json()["file_hash"])
        self.wait_for_no_lease("default", doc_id)
        source = self.app.state.settings.data_dir / "uploads" / job_id / "crash.pdf"
        chunk = Chunk("one", doc_id, "logical", "default", "crash.pdf", 1, 1, "", 0, "合成内容", "hash", ())
        prepared = PreparedIngestion(ParsedDocument(doc_id, "logical", "crash.pdf", response.json()["file_hash"], 1, (), ()), (chunk,), self.app.state.settings.data_dir / "normalized" / doc_id)
        class FakeMilvus:
            def __init__(self):
                self.indexed = False
                self.cleanup_fails = True
            def document_chunks(self, kb, document_id):
                return []
            def has_collection(self):
                return self.indexed
            def upsert(self, chunks):
                self.indexed = True
            def delete_document(self, kb, document_id):
                if self.cleanup_fails:
                    raise RuntimeError("simulated Milvus outage")
                self.indexed = False
        fake = FakeMilvus()
        embedder = type("Embedder", (), {"embed": lambda self, texts: [[0.1] * 4096 for _ in texts]})()
        with patch("knowledge_rag.document_import.IngestionPipeline.prepare", return_value=prepared), patch("knowledge_rag.document_import.create_embedder", return_value=embedder), patch("knowledge_rag.document_import.MilvusChunkStore", return_value=fake), patch("knowledge_rag.document_import.IngestionPipeline.save_prepared", side_effect=OSError("simulated disk failure")):
            self.app.state.document_import_manager._run(job_id, source, "native")
            self.assertEqual(self.app.state.document_import_manager.get(job_id).status, "failed")
            intent = self.app.state.settings.data_dir / "index-intents" / f"{job_id}.json"
            self.assertTrue(intent.exists())
            self.assertTrue(fake.indexed)
            self.assertTrue(source.exists())
            self.assertEqual(self.client.post(f"/api/v1/admin/document-imports/{job_id}/retry").status_code, 409)
            self.assertIn("索引清理未完成", self.client.get(f"/api/v1/admin/document-imports/{job_id}").json()["error"])
            fake.cleanup_fails = False
            restarted = DocumentImportManager(self.app.state.settings, self.store)
            self.assertFalse(fake.indexed)
            self.assertFalse(intent.exists())
            repaired = restarted.get(job_id)
            assert repaired is not None
            self.assertEqual(repaired.status, "failed")
            self.assertEqual(repaired.error, "索引清理已完成，可重试")
            self.assertTrue(source.exists())

    def test_stale_intent_cannot_delete_newer_completed_import(self) -> None:
        from knowledge_rag.db import DocumentImportRecord
        from knowledge_rag.document_import import DocumentImportManager, now
        from knowledge_rag.index_commit import IndexIntent
        digest = hashlib.sha256(b"newer success").hexdigest()
        old_id, new_id = "old-job", "new-job"
        stamp = now()
        with Session(self.store.engine) as session, session.begin():
            session.add(DocumentImportRecord(id=old_id, file_name="a.pdf", knowledge_base_id="default", file_hash=digest,
                                             status="failed", total_chunks=0, completed_chunks=0, error="crashed",
                                             created_at="2020-01-01T00:00:00+00:00", updated_at=stamp))
            session.add(DocumentImportRecord(id=new_id, file_name="a.pdf", knowledge_base_id="default", file_hash=digest,
                                             status="completed", total_chunks=1, completed_chunks=1, error="",
                                             created_at=stamp, updated_at=stamp))
        intent = IndexIntent(old_id, "default", stable_id("document", digest), "test_collection", False)
        intent.persist(self.app.state.settings.data_dir)
        with patch("knowledge_rag.document_import.MilvusChunkStore", side_effect=AssertionError("不能删除后来成功的索引")):
            DocumentImportManager(self.app.state.settings, self.store)
        self.assertFalse(intent.path(self.app.state.settings.data_dir).exists())

    def test_completed_status_failure_rolls_back_new_index_and_cache(self) -> None:
        from knowledge_rag.models import Chunk, ParsedDocument
        from knowledge_rag.pipeline import PreparedIngestion
        self.login("admin", "strong-password")
        with patch("knowledge_rag.document_import.DocumentImportManager._run", side_effect=lambda job, path, mode, lease: lease.close()):
            response = self.client.post("/api/v1/admin/document-imports", data={"knowledge_base_id": "default", "ocr_mode": "native"}, files={"file": ("status.pdf", b"synthetic")})
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["id"]
        doc_id = stable_id("document", response.json()["file_hash"])
        self.wait_for_no_lease("default", doc_id)
        source = self.app.state.settings.data_dir / "uploads" / job_id / "status.pdf"
        output = self.app.state.settings.data_dir / "normalized" / doc_id
        prepared = PreparedIngestion(
            ParsedDocument(doc_id, "logical", "status.pdf", response.json()["file_hash"], 1, (), ()),
            (Chunk("one", doc_id, "logical", "default", "status.pdf", 1, 1, "", 0, "内容", "hash", ()),), output,
        )
        class FakeMilvus:
            def __init__(self): self.indexed = False
            def document_chunks(self, kb, document_id): return []
            def has_collection(self): return self.indexed
            def upsert(self, chunks): self.indexed = True
            def delete_document(self, kb, document_id): self.indexed = False
        fake = FakeMilvus()
        embedder = type("Embedder", (), {"embed": lambda self, texts: [[0.1] * 4096 for _ in texts]})()
        manager = self.app.state.document_import_manager
        normal_update = manager.update
        def fail_completed(id, **values):
            if values.get("status") == "completed":
                raise RuntimeError("simulated DB outage at commit")
            return normal_update(id, **values)
        with patch("knowledge_rag.document_import.IngestionPipeline.prepare", return_value=prepared), patch("knowledge_rag.document_import.create_embedder", return_value=embedder), patch("knowledge_rag.document_import.MilvusChunkStore", return_value=fake), patch.object(manager, "update", side_effect=fail_completed):
            manager._run(job_id, source, "native")
        self.assertEqual(manager.get(job_id).status, "failed")
        self.assertFalse(fake.indexed)
        self.assertEqual(json.loads((output / "chunks.json").read_text()), [])
        self.assertTrue(source.exists())
        self.assertFalse((self.app.state.settings.data_dir / "index-intents" / f"{job_id}.json").exists())

    def test_document_management_is_admin_only_scoped_and_removes_cached_chunks(self) -> None:
        digest = hashlib.sha256(b"fixture").hexdigest()
        doc_id = stable_id("document", digest)
        rows = [{"document_id": doc_id, "document_name": "测试.pdf", "page_start": 1, "page_end": 1, "section_title": "首页", "content": "正文"}]
        cache = self.app.state.settings.data_dir / "normalized" / doc_id
        cache.mkdir(parents=True)
        (cache / "document.json").write_text(json.dumps({"file_hash": digest}))
        (cache / "chunks.json").write_text(json.dumps([{"document_id": doc_id, "knowledge_base_id": "default", "content": "正文"}]))

        class FakeMilvus:
            def document_chunks(self, kb, requested_id=None, include_content=False):
                return rows if kb == "default" and (requested_id is None or requested_id == doc_id) else []

            def delete_document(self, kb, requested_id):
                self.deleted = (kb, requested_id)
                rows.clear()

        milvus = FakeMilvus()
        url = "/api/v1/admin/documents"
        with patch("knowledge_rag.document_import.DocumentImportManager._milvus", return_value=milvus), patch("knowledge_rag.document_import.DocumentImportManager._run_reindex", side_effect=lambda job, doc_id, lease: lease.close()):
            self.assertEqual(self.client.get(url, params={"knowledge_base_id": "default"}).status_code, 401)
            self.login("reader", "reader-password")
            self.assertEqual(self.client.get(url, params={"knowledge_base_id": "default"}).status_code, 403)
            self.login("admin", "strong-password")
            self.assertEqual(len(self.client.get(url, params={"knowledge_base_id": "default"}).json()), 1)
            self.assertEqual(self.client.get(f"{url}/{doc_id}", params={"knowledge_base_id": "other"}).status_code, 404)
            detail = self.client.get(f"{url}/{doc_id}", params={"knowledge_base_id": "default"}).json()
            self.assertEqual(detail["chunks"][0]["content"], "正文")
            rebuild = self.client.post(f"{url}/{doc_id}/reindex", params={"knowledge_base_id": "default"})
            self.assertEqual(rebuild.status_code, 202, rebuild.text)
            self.assertEqual(rebuild.json()["total_chunks"], 1)
            self.wait_for_no_lease("default", doc_id)
            self.assertEqual(self.client.delete(f"{url}/{doc_id}", params={"knowledge_base_id": "default"}).status_code, 409)
            from knowledge_rag.document_import import DocumentImportManager
            DocumentImportManager(self.app.state.settings, self.store).update(rebuild.json()["id"], status="completed")
            self.assertEqual(self.client.delete(f"{url}/{doc_id}", params={"knowledge_base_id": "default"}).status_code, 204)
            self.assertEqual(milvus.deleted, ("default", doc_id))
            self.assertEqual(json.loads((cache / "chunks.json").read_text()), [])
            self.assertEqual(self.client.get(url, params={"knowledge_base_id": "default"}).json(), [])

    def test_cached_chunks_survive_interrupted_atomic_replacement(self) -> None:
        from knowledge_rag.models import Chunk, ParsedDocument
        from knowledge_rag.pipeline import IngestionPipeline, PreparedIngestion
        output = self.app.state.settings.data_dir / "normalized" / "stable"
        document = ParsedDocument("stable", "logical", "stable.pdf", "hash", 1, (), ())
        def prepared(text: str) -> PreparedIngestion:
            chunk = Chunk("chunk", "stable", "logical", "default", "stable.pdf", 1, 1, "", 0, text, "hash", ())
            return PreparedIngestion(document, (chunk,), output)
        IngestionPipeline.save_prepared(prepared("旧内容"))
        old = (output / "chunks.json").read_bytes()
        from knowledge_rag.index_commit import os as commit_os
        replace = commit_os.replace
        def fail_chunk_replace(source, target):
            if str(target).endswith("chunks.json"):
                raise OSError("simulated power loss during cache promotion")
            return replace(source, target)
        with patch("knowledge_rag.index_commit.os.replace", side_effect=fail_chunk_replace), self.assertRaises(OSError):
            IngestionPipeline.save_prepared(prepared("新内容"))
        self.assertEqual((output / "chunks.json").read_bytes(), old)
        self.assertEqual(list(output.glob(".chunks.json.*")), [])

    def test_cached_chunks_preserve_other_knowledge_bases(self) -> None:
        from knowledge_rag.models import Chunk, ParsedDocument
        from knowledge_rag.pipeline import IngestionPipeline, PreparedIngestion
        output = self.app.state.settings.data_dir / "normalized" / "shared"
        document = ParsedDocument("shared", "logical", "shared.pdf", "hash", 1, (), ())
        def prepared(kb: str) -> PreparedIngestion:
            chunk = Chunk(f"chunk-{kb}", "shared", "logical", kb, "shared.pdf", 1, 1, "", 0, "内容", "hash", ())
            return PreparedIngestion(document, (chunk,), output)
        IngestionPipeline.save_prepared(prepared("default"))
        IngestionPipeline.save_prepared(prepared("other"))
        IngestionPipeline.save_prepared(prepared("default"))
        chunks = json.loads((output / "chunks.json").read_text())
        self.assertEqual({item["knowledge_base_id"] for item in chunks}, {"default", "other"})
        self.assertEqual(len(chunks), 2)

    def test_account_locale_is_persisted_and_scoped_to_current_user(self) -> None:
        endpoint = "/api/v1/account/preferences"
        self.assertEqual(self.client.patch(endpoint, json={"preferred_locale": "en"}).status_code, 401)
        self.login("reader", "reader-password")
        self.assertEqual(self.client.get("/api/v1/auth/me").json()["preferred_locale"], "zh-CN")
        response = self.client.patch(endpoint, json={"preferred_locale": "en"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["preferred_locale"], "en")
        self.assertEqual(self.client.patch(endpoint, json={"preferred_locale": "fr"}).status_code, 422)
        self.client.post("/api/v1/auth/logout")
        self.login("reader", "reader-password")
        self.assertEqual(self.client.get("/api/v1/auth/me").json()["preferred_locale"], "en")
        self.client.post("/api/v1/auth/logout")
        self.login("admin", "strong-password")
        self.assertEqual(self.client.get("/api/v1/auth/me").json()["preferred_locale"], "zh-CN")

    def test_account_profile_and_password_revoke_all_sessions(self) -> None:
        self.login("reader", "reader-password")
        token = self.store.create_session(self.user.id, 24)
        response = self.client.patch("/api/v1/account/profile", json={"display_name": " 新名称 "})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["display_name"], "新名称")
        self.assertEqual(self.client.patch("/api/v1/account/profile", json={"display_name": "   "}).status_code, 400)
        endpoint = "/api/v1/account/password"
        self.assertEqual(self.client.post(endpoint, json={"current_password": "wrong", "new_password": "new-password"}).status_code, 400)
        self.assertIsNotNone(self.store.get_session_user(token))
        self.assertEqual(self.client.post(endpoint, json={"current_password": "reader-password", "new_password": "reader-password"}).status_code, 400)
        self.assertEqual(self.client.post(endpoint, json={"current_password": "reader-password", "new_password": "new-password"}).status_code, 204)
        self.assertIsNone(self.store.get_session_user(token))
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
        self.assertIsNone(self.store.authenticate("reader", "reader-password"))
        self.login("reader", "new-password")
        self.assertEqual(self.client.get("/api/v1/auth/me").json()["display_name"], "新名称")

    def test_chat_requires_login(self) -> None:
        response = self.client.post("/api/v1/chat", json={"question": "测试", "knowledge_base_id": "default"})
        self.assertEqual(response.status_code, 401)

    def test_authenticated_user_can_chat_but_cannot_manage_models(self) -> None:
        self.login("reader", "reader-password")
        chat = self.client.post("/api/v1/chat", json={"question": "测试", "knowledge_base_id": "default"})
        self.assertEqual(chat.status_code, 200)
        profiles = self.client.get("/api/v1/admin/model-profiles")
        self.assertEqual(profiles.status_code, 403)

    def test_user_without_knowledge_base_access_is_forbidden(self) -> None:
        self.store.set_user_knowledge_bases(self.user.id, [], self.admin.id)
        self.login("reader", "reader-password")
        response = self.client.post("/api/v1/chat", json={"question": "测试", "knowledge_base_id": "default"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "没有访问该知识库的权限")

    def test_disabling_user_invalidates_existing_session(self) -> None:
        self.login("reader", "reader-password")
        self.store.set_user_active(self.user.id, False, self.admin.id)
        response = self.client.get("/api/v1/auth/me")
        self.assertEqual(response.status_code, 401)

    def test_admin_can_create_encrypted_profile_without_key_disclosure(self) -> None:
        self.login("admin", "strong-password")
        response = self.client.post(
            "/api/v1/admin/model-profiles",
            json={
                "name": "远程模型",
                "purpose": "chat",
                "protocol": "openai_compatible",
                "base_url": "https://example.com/v1",
                "api_key": "sk-secret-value",
                "model_name": "example-chat",
                "options": {"temperature": 0.1},
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertTrue(payload["has_api_key"])
        self.assertNotIn("api_key", payload)
        stored = self.store.get_profile(payload["id"])
        assert stored is not None
        self.assertEqual(stored.api_key, "sk-secret-value")
        raw_database = Path(self.temp_dir.name, "app.db").read_bytes()
        self.assertNotIn(b"sk-secret-value", raw_database)

    def test_admin_can_manage_user_access_status_and_password(self) -> None:
        self.login("admin", "strong-password")
        created = self.client.post(
            "/api/v1/admin/users",
            json={
                "username": "new-reader",
                "display_name": "新用户",
                "password": "initial-password",
                "role": "user",
                "knowledge_base_ids": ["default"],
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        user_id = created.json()["id"]
        self.assertEqual(created.json()["knowledge_base_ids"], ["default"])

        access = self.client.put(f"/api/v1/admin/users/{user_id}/knowledge-bases", json={"knowledge_base_ids": []})
        self.assertEqual(access.status_code, 200)
        self.assertEqual(access.json()["knowledge_base_ids"], [])

        reset = self.client.post(f"/api/v1/admin/users/{user_id}/reset-password", json={"password": "replacement-password"})
        self.assertEqual(reset.status_code, 204)
        self.assertIsNotNone(self.store.authenticate("new-reader", "replacement-password"))

        disabled = self.client.patch(f"/api/v1/admin/users/{user_id}/status", json={"active": False})
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(disabled.json()["active"])
        self.assertIsNone(self.store.authenticate("new-reader", "replacement-password"))

        self_disable = self.client.patch(f"/api/v1/admin/users/{self.admin.id}/status", json={"active": False})
        self.assertEqual(self_disable.status_code, 409)

    def test_admin_can_configure_activate_and_disable_reranker(self) -> None:
        self.login("admin", "strong-password")
        created = self.client.post(
            "/api/v1/admin/model-profiles",
            json={
                "name": "BGE 中文重排序",
                "purpose": "reranker",
                "protocol": "jina_compatible",
                "base_url": "https://api.example.com/v1",
                "model_name": "BAAI/bge-reranker-v2-m3",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        profile_id = created.json()["id"]
        activated = self.client.post(f"/api/v1/admin/model-profiles/{profile_id}/activate-reranker")
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertTrue(activated.json()["is_active"])
        self.assertEqual(self.store.get_setting("active_reranker_profile_id"), profile_id)

        disabled = self.client.post("/api/v1/admin/model-profiles/deactivate-reranker")
        self.assertEqual(disabled.status_code, 204)
        self.assertEqual(self.store.get_setting("active_reranker_profile_id"), "")

        local = self.client.post(
            "/api/v1/admin/model-profiles",
            json={
                "name": "本地 Qwen3 重排序",
                "purpose": "reranker",
                "protocol": "ollama_reranker",
                "base_url": "http://127.0.0.1:11434",
                "model_name": "dengcao/Qwen3-Reranker-8B:Q4_K_M",
            },
        )
        self.assertEqual(local.status_code, 201, local.text)
        self.assertEqual(local.json()["protocol"], "ollama_reranker")

    def test_admin_can_activate_chat_profile(self) -> None:
        self.login("admin", "strong-password")
        created = self.client.post(
            "/api/v1/admin/model-profiles",
            json={
                "name": "备用 Ollama",
                "purpose": "chat",
                "protocol": "ollama",
                "base_url": "http://127.0.0.1:11434",
                "model_name": "qwen3.5:9b",
            },
        ).json()
        response = self.client.post(f"/api/v1/admin/model-profiles/{created['id']}/activate-chat")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["is_active"])
        self.assertEqual(self.store.get_setting("active_chat_profile_id"), created["id"])


if __name__ == "__main__":
    unittest.main()

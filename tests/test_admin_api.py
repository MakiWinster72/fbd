from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import hashlib
import json

from knowledge_rag.documents import stable_id

from fastapi.testclient import TestClient

from knowledge_rag.admin_store import AdminStore
from knowledge_rag.api import create_app
from knowledge_rag.config import Settings
from knowledge_rag.rag import ChatResult


class FakeRagService:
    def retrieve(self, question: str, knowledge_base_id: str):
        return ()

    def answer(self, question: str, knowledge_base_id: str):
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
        self.client = TestClient(create_app(rag_service=FakeRagService(), settings=settings, admin_store=self.store))

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def login(self, username: str, password: str) -> None:
        response = self.client.post("/api/v1/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)

    def test_document_import_requires_admin_and_validates_upload(self) -> None:
        endpoint = "/api/v1/admin/document-imports"
        payload = {"knowledge_base_id": "default", "ocr_mode": "native"}
        self.assertEqual(self.client.post(endpoint, data=payload, files={"file": ("a.pdf", b"data")}).status_code, 401)
        self.login("reader", "reader-password")
        self.assertEqual(self.client.post(endpoint, data=payload, files={"file": ("a.pdf", b"data")}).status_code, 403)
        self.login("admin", "strong-password")
        self.assertEqual(self.client.post(endpoint, data=payload, files={"file": ("a.doc", b"data")}).status_code, 400)
        self.assertEqual(self.client.post(endpoint, data=payload, files={"file": ("a.pdf", b"")}).status_code, 400)
        with patch("knowledge_rag.document_import.DocumentImportManager._run"):
            response = self.client.post(endpoint, data=payload, files={"file": ("a.pdf", b"data")})
            self.assertEqual(response.status_code, 202, response.text)
            job_id = response.json()["id"]
            self.assertEqual(self.client.get(f"{endpoint}/{job_id}").json()["status"], "queued")
            duplicate = self.client.post(endpoint, data=payload, files={"file": ("a.pdf", b"data")})
            self.assertEqual(duplicate.status_code, 202)
            self.assertEqual(duplicate.json()["id"], job_id)
            self.assertEqual(len(self.client.get(endpoint).json()), 1)
            from knowledge_rag.document_import import DocumentImportManager
            DocumentImportManager(self.client.app.state.settings, self.store).update(job_id, status="completed")
            skipped = self.client.post(endpoint, data=payload, files={"file": ("a.pdf", b"data")})
            self.assertEqual(skipped.json()["id"], job_id)
            replaced = self.client.post(endpoint, data={**payload, "duplicate_mode": "replace"}, files={"file": ("a.pdf", b"data")})
            self.assertNotEqual(replaced.json()["id"], job_id)
            self.assertEqual(len(self.client.get(endpoint).json()), 2)

    def test_failed_import_retains_file_and_retries_original_job(self) -> None:
        endpoint = "/api/v1/admin/document-imports"
        self.login("admin", "strong-password")
        self.assertEqual(self.client.post(f"{endpoint}/missing/retry").status_code, 404)
        with patch("knowledge_rag.document_import.DocumentImportManager._run"):
            first = self.client.post(endpoint, data={"knowledge_base_id": "default", "ocr_mode": "native"}, files={"file": ("fixture.pdf", b"original")})
            self.assertEqual(first.status_code, 202, first.text)
            job_id = first.json()["id"]
            path = self.client.app.state.settings.data_dir / "uploads" / job_id / "fixture.pdf"
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(json.loads((path.parent / "options.json").read_text())["ocr_mode"], "native")
            collision = self.client.post(endpoint, data={"knowledge_base_id": "default", "duplicate_mode": "replace"}, files={"file": ("fixture.pdf", b"original")})
            self.assertEqual(collision.status_code, 409)
            self.assertEqual(path.read_bytes(), b"original")
            manager = self.client.app.state.document_import_manager
            from knowledge_rag.document_import import DocumentImportManager
            DocumentImportManager(self.client.app.state.settings, self.store)
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

    def test_embedding_failure_does_not_write_partial_index_and_retry_cleans_up(self) -> None:
        from knowledge_rag.models import Chunk, ParsedDocument
        from knowledge_rag.pipeline import PreparedIngestion
        self.login("admin", "strong-password")
        with patch("knowledge_rag.document_import.DocumentImportManager._run"):
            response = self.client.post("/api/v1/admin/document-imports", data={"knowledge_base_id": "default", "ocr_mode": "native"}, files={"file": ("fixture.pdf", b"original")})
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["id"]
        path = self.client.app.state.settings.data_dir / "uploads" / job_id / "fixture.pdf"
        chunks = tuple(Chunk(str(index), "doc", "logical", "default", "fixture.pdf", 1, 1, "", index, "正文", "hash", ()) for index in range(9))
        document = ParsedDocument("doc", "logical", "fixture.pdf", response.json()["file_hash"], 1, (), ())
        prepared = PreparedIngestion(document, chunks, self.client.app.state.settings.data_dir / "normalized" / "doc")
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
            def upsert(self, chunks):
                self.writes.append(chunks)
                if self.fail_write:
                    raise RuntimeError("Milvus write interrupted")
            def delete_document(self, kb, doc_id):
                self.rollbacks += 1
                self.writes.clear()
        embedder = Embedder()
        milvus = Milvus()
        manager = self.client.app.state.document_import_manager
        with patch("knowledge_rag.document_import.IngestionPipeline.prepare", return_value=prepared), patch("knowledge_rag.document_import.create_embedder", return_value=embedder), patch("knowledge_rag.document_import.MilvusChunkStore", return_value=milvus):
            manager._run(job_id, path, "native")
            self.assertEqual(manager.get(job_id).status, "failed")
            self.assertEqual(len(milvus.writes), 0)
            self.assertTrue(path.exists())
            embedder.fail = False
            milvus.fail_write = True
            with patch("knowledge_rag.document_import.DocumentImportManager._run"):
                retried = self.client.post(f"/api/v1/admin/document-imports/{job_id}/retry")
            self.assertEqual(retried.status_code, 202, retried.text)
            manager._run(job_id, path, "native")
            self.assertEqual(manager.get(job_id).status, "failed")
            self.assertEqual(milvus.rollbacks, 1)
            self.assertTrue(path.exists())
            milvus.fail_write = False
            with patch("knowledge_rag.document_import.DocumentImportManager._run"):
                self.assertEqual(self.client.post(f"/api/v1/admin/document-imports/{job_id}/retry").status_code, 202)
            manager._run(job_id, path, "native")
        self.assertEqual(manager.get(job_id).status, "completed")
        self.assertEqual(len(milvus.writes), 1)
        self.assertEqual(len(milvus.writes[0]), 9)
        self.assertFalse(path.exists())
        self.assertFalse(path.parent.exists())

    def test_document_management_is_admin_only_scoped_and_removes_cached_chunks(self) -> None:
        digest = hashlib.sha256(b"fixture").hexdigest()
        doc_id = stable_id("document", digest)
        rows = [{"document_id": doc_id, "document_name": "测试.pdf", "page_start": 1, "page_end": 1, "section_title": "首页", "content": "正文"}]
        cache = self.client.app.state.settings.data_dir / "normalized" / doc_id
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
        with patch("knowledge_rag.document_import.DocumentImportManager._milvus", return_value=milvus), patch("knowledge_rag.document_import.DocumentImportManager._run_reindex"):
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
            self.assertEqual(self.client.delete(f"{url}/{doc_id}", params={"knowledge_base_id": "default"}).status_code, 409)
            from knowledge_rag.document_import import DocumentImportManager
            DocumentImportManager(self.client.app.state.settings, self.store).update(rebuild.json()["id"], status="completed")
            self.assertEqual(self.client.delete(f"{url}/{doc_id}", params={"knowledge_base_id": "default"}).status_code, 204)
            self.assertEqual(milvus.deleted, ("default", doc_id))
            self.assertEqual(json.loads((cache / "chunks.json").read_text()), [])
            self.assertEqual(self.client.get(url, params={"knowledge_base_id": "default"}).json(), [])

    def test_cached_chunks_preserve_other_knowledge_bases(self) -> None:
        from knowledge_rag.models import Chunk, ParsedDocument
        from knowledge_rag.pipeline import IngestionPipeline, PreparedIngestion
        output = self.client.app.state.settings.data_dir / "normalized" / "shared"
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

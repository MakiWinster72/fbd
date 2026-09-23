from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

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

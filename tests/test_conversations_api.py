from __future__ import annotations

import secrets
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from knowledge_rag.admin_store import AdminStore
from knowledge_rag.api import create_app
from knowledge_rag.config import Settings
from knowledge_rag.db import ConversationTurnRecord
from knowledge_rag.rag import ChatResult, RetrievedSource


class ConversationsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = replace(Settings(), app_db_path=Path(directory.name) / "app.db", data_dir=Path(directory.name) / "data", model_secret_key=secrets.token_urlsafe(24), auth_required=True)
        self.store = AdminStore(self.settings.app_db_path, self.settings.model_secret_key)
        self.addCleanup(self.store.engine.dispose)
        self.store.seed_defaults(self.settings)
        self.reader = self.store.create_user("reader", "读者", "reader-password", "user")
        self.other = self.store.create_user("other", "另一账号", "other-password", "admin")
        self.store.set_user_knowledge_bases(self.reader.id, ["default"], self.other.id)
        self.app = create_app(settings=self.settings, admin_store=self.store)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.login("reader", "reader-password")

    def login(self, username: str, password: str) -> None:
        response = self.client.post("/api/v1/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)

    def conversation(self, title: str = "测试会话") -> str:
        response = self.client.post("/api/v1/conversations", json={"title": title, "knowledge_base_id": "default"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def detail(self, identifier: str) -> dict:
        response = self.client.get(f"/api/v1/conversations/{identifier}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        return response.json()

    def test_persists_refusal_and_locale_and_restores_after_app_restart(self) -> None:
        identifier = self.conversation()
        with patch("knowledge_rag.api.get_rag_service") as get_service:
            get_service.return_value.answer.return_value = ChatResult("Insufficient evidence.", False, ())
            response = self.client.post("/api/v1/chat", json={"question": "What is unknown?", "conversation_id": identifier, "locale": "en"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["conversation_id"], identifier)
        detail = self.detail(identifier)
        self.assertEqual(detail["conversation"]["turn_count"], 1)
        self.assertEqual(detail["turns"][0]["locale"], "en")
        self.assertEqual(detail["turns"][0]["answer"], "Insufficient evidence.")
        self.assertFalse(detail["turns"][0]["grounded"])
        self.assertEqual(detail["turns"][0]["sources"], [])
        reopened = AdminStore(self.settings.app_db_path, self.settings.model_secret_key)
        self.addCleanup(reopened.engine.dispose)
        with TestClient(create_app(settings=self.settings, admin_store=reopened)) as restarted:
            restarted.post("/api/v1/auth/login", json={"username": "reader", "password": "reader-password"})
            self.assertEqual(restarted.get(f"/api/v1/conversations/{identifier}").json(), detail)

    def test_other_account_even_admin_cannot_read_append_or_delete(self) -> None:
        identifier = self.conversation()
        self.login("other", "other-password")
        self.assertEqual(self.client.get("/api/v1/conversations").json()["items"], [])
        with patch("knowledge_rag.api.get_rag_service") as service:
            self.assertEqual(self.client.get(f"/api/v1/conversations/{identifier}").status_code, 404)
            self.assertEqual(self.client.delete(f"/api/v1/conversations/{identifier}").status_code, 404)
            self.assertEqual(self.client.post("/api/v1/chat", json={"question": "private", "conversation_id": identifier}).status_code, 404)
            service.assert_not_called()

    def test_revoked_permission_hides_history_and_owner_can_erase_it(self) -> None:
        identifier = self.conversation()
        self.store.set_user_knowledge_bases(self.reader.id, [], self.other.id)
        self.assertEqual(self.client.get("/api/v1/conversations").json()["items"], [])
        self.assertEqual(self.client.get(f"/api/v1/conversations/{identifier}").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/v1/conversations/{identifier}").status_code, 204)

    def test_no_history_without_conversation_id_or_for_anonymous_development(self) -> None:
        with patch("knowledge_rag.api.get_rag_service") as service:
            service.return_value.answer.return_value = ChatResult("No evidence.", False, ())
            self.assertEqual(self.client.post("/api/v1/chat", json={"question": "stateless"}).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/conversations").json()["items"], [])
        with TestClient(create_app(settings=self.settings, admin_store=self.store, auth_required=False)) as development:
            self.assertEqual(development.post("/api/v1/conversations", json={"title": "anonymous"}).status_code, 401)

    def test_failed_model_does_not_save_partial_turn(self) -> None:
        identifier = self.conversation()
        with patch("knowledge_rag.api.get_rag_service") as service:
            service.return_value.answer.side_effect = RuntimeError("provider unavailable")
            self.assertEqual(self.client.post("/api/v1/chat", json={"question": "failure", "conversation_id": identifier}).status_code, 503)
        self.assertEqual(self.detail(identifier)["turns"], [])

    def test_source_snapshots_and_turn_order_survive_later_answers_and_delete_cascades(self) -> None:
        identifier = self.conversation()
        for name in ("first.pdf", "second.pdf"):
            source = RetrievedSource(index=1, score=.8, document_id=name, document_name=name, page_start=4, page_end=5, section_title="Evidence", excerpt="Saved excerpt", content="PRIVATE FULL CONTENT", vector_score=.8, rerank_score=.7)
            with patch("knowledge_rag.api.get_rag_service") as service:
                service.return_value.answer.return_value = ChatResult(f"Answer for {name}【1】", True, (source,))
                response = self.client.post("/api/v1/chat", json={"question": name, "conversation_id": identifier})
            self.assertEqual(response.status_code, 200, response.text)
        detail = self.detail(identifier)
        self.assertEqual([turn["question"] for turn in detail["turns"]], ["first.pdf", "second.pdf"])
        self.assertEqual(detail["turns"][0]["sources"][0]["document_name"], "first.pdf")
        self.assertNotIn("PRIVATE FULL CONTENT", str(detail))
        self.assertNotIn("content", detail["turns"][0]["sources"][0])
        self.assertEqual(self.client.delete(f"/api/v1/conversations/{identifier}").status_code, 204)
        with Session(self.store.engine) as session:
            self.assertEqual(session.scalar(select(func.count(ConversationTurnRecord.id))), 0)

    def test_full_conversation_rejects_before_calling_model(self) -> None:
        identifier = self.conversation()
        with patch("knowledge_rag.conversation_store.MAX_CONVERSATION_TURNS", 1), patch("knowledge_rag.api.MAX_CONVERSATION_TURNS", 1):
            self.app.state.conversation_store.append(self.reader.id, identifier, "first", "answer", False, "en", [])
            with patch("knowledge_rag.api.get_rag_service") as service:
                response = self.client.post("/api/v1/chat", json={"question": "too many", "conversation_id": identifier})
                self.assertEqual(response.status_code, 400, response.text)
                service.assert_not_called()
        self.assertEqual(self.detail(identifier)["conversation"]["turn_count"], 1)

    def test_failed_history_write_does_not_return_unsaved_answer_or_error_details(self) -> None:
        identifier = self.conversation()
        with patch("knowledge_rag.api.get_rag_service") as service, patch.object(self.app.state.conversation_store, "append", side_effect=RuntimeError("SECRET DATABASE ERROR")):
            service.return_value.answer.return_value = ChatResult("UNSAVED ANSWER", False, ())
            response = self.client.post("/api/v1/chat", json={"question": "failure", "conversation_id": identifier})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertNotIn("SECRET", response.text)
        self.assertNotIn("UNSAVED ANSWER", response.text)
        self.assertEqual(self.detail(identifier)["turns"], [])

    def test_grant_revoked_while_model_runs_does_not_save_or_return_answer(self) -> None:
        identifier = self.conversation()
        def answer(*args):
            self.store.set_user_knowledge_bases(self.reader.id, [], self.other.id)
            return ChatResult("REVOKED ANSWER", False, ())
        with patch("knowledge_rag.api.get_rag_service") as service:
            service.return_value.answer.side_effect = answer
            response = self.client.post("/api/v1/chat", json={"question": "race", "conversation_id": identifier})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertNotIn("REVOKED ANSWER", response.text)
        self.assertEqual(self.client.delete(f"/api/v1/conversations/{identifier}").status_code, 204)

    def test_pagination_and_title_validation(self) -> None:
        identifiers = [self.conversation(f"Session {index}") for index in range(3)]
        first = self.client.get("/api/v1/conversations?limit=2&knowledge_base_id=default").json()
        second = self.client.get("/api/v1/conversations?limit=2&offset=2&knowledge_base_id=default").json()
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])
        self.assertEqual([item["id"] for item in first["items"] + second["items"]], identifiers[::-1])
        self.assertEqual(self.client.post("/api/v1/conversations", json={"title": "   "}).status_code, 400)
        self.assertEqual(self.client.get("/api/v1/conversations?limit=101").status_code, 422)
        self.assertEqual(self.client.get("/api/v1/conversations?offset=-1").status_code, 422)

    def test_corrupt_saved_json_fails_explicitly_instead_of_dropping_citations(self) -> None:
        identifier = self.conversation()
        self.app.state.conversation_store.append(self.reader.id, identifier, "question", "answer", True, "en", [])
        with Session(self.store.engine) as session, session.begin():
            session.execute(update(ConversationTurnRecord).where(ConversationTurnRecord.conversation_id == identifier).values(sources_json="NOT VALID JSON"))
        response = self.client.get(f"/api/v1/conversations/{identifier}")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"], "会话历史暂不可用")

    def test_delete_during_model_call_does_not_resurrect_conversation(self) -> None:
        identifier = self.conversation()
        def answer(*args):
            self.assertEqual(self.client.delete(f"/api/v1/conversations/{identifier}").status_code, 204)
            return ChatResult("No evidence.", False, ())
        with patch("knowledge_rag.api.get_rag_service") as service:
            service.return_value.answer.side_effect = answer
            self.assertEqual(self.client.post("/api/v1/chat", json={"question": "race", "conversation_id": identifier}).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/conversations").json()["items"], [])

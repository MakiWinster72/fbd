from __future__ import annotations

import unittest
from typing import cast

from fastapi.testclient import TestClient

from knowledge_rag.api import create_app
from knowledge_rag.rag import ChatResult, RagService, RetrievedSource


SOURCE = RetrievedSource(
    index=1,
    score=0.71,
    document_id="doc-1",
    document_name="设备清单.pdf",
    page_start=1,
    page_end=1,
    section_title="项目预算表",
    excerpt="项目预算为 120 万元。",
    content="项目预算为 120 万元。",
)


class FakeRagService:
    def retrieve(self, question: str, knowledge_base_id: str):
        return (SOURCE,)

    def answer(self, question: str, knowledge_base_id: str, locale: str = "zh-CN"):
        answer = "Budget is 1.2 million yuan【1】." if locale == "en" else "项目预算为 120 万元【1】。"
        return ChatResult(answer, True, (SOURCE,))


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app(rag_service=cast(RagService, FakeRagService()), auth_required=False))

    def test_search_returns_sources_without_full_content(self) -> None:
        response = self.client.post(
            "/api/v1/search",
            json={"question": "项目预算是多少？", "knowledge_base_id": "default"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["sources"][0]["document_name"], "设备清单.pdf")
        self.assertNotIn("content", payload["sources"][0])

    def test_chat_returns_grounded_answer_and_sources(self) -> None:
        response = self.client.post(
            "/api/v1/chat",
            json={"question": "项目预算是多少？", "knowledge_base_id": "default"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["grounded"])
        self.assertIn("【1】", payload["answer"])
        self.assertEqual(payload["sources"][0]["page_start"], 1)

    def test_chat_timeout_returns_retryable_error(self) -> None:
        from unittest.mock import patch

        with patch.object(FakeRagService, "answer", side_effect=TimeoutError("The read operation timed out")):
            response = self.client.post("/api/v1/chat", json={"question": "项目预算是多少？"})
        self.assertEqual(response.status_code, 504)
        self.assertIn("超时", response.json()["detail"])
        self.assertNotIn("answer", response.json())

    def test_chat_accepts_english_and_rejects_unknown_locale(self) -> None:
        response = self.client.post("/api/v1/chat", json={"question": "Budget?", "locale": "en"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Budget", response.json()["answer"])
        invalid = self.client.post("/api/v1/chat", json={"question": "Budget?", "locale": "fr"})
        self.assertEqual(invalid.status_code, 422)

    def test_invalid_knowledge_base_id_is_rejected(self) -> None:
        response = self.client.post(
            "/api/v1/search",
            json={"question": "test", "knowledge_base_id": 'x" or true'},
        )
        self.assertEqual(response.status_code, 422)

    def test_blank_question_is_rejected(self) -> None:
        response = self.client.post(
            "/api/v1/chat",
            json={"question": "   ", "knowledge_base_id": "default"},
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()

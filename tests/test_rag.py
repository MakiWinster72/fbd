from __future__ import annotations

import unittest

from knowledge_rag.rag import RagService


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]


class FakeStore:
    def __init__(self, hits: list[dict]) -> None:
        self.hits = hits
        self.last_filter: str | None = None
        self.last_limit: int | None = None

    def search(self, vector: list[float], limit: int = 10, knowledge_base_id: str | None = None) -> list[dict]:
        self.last_filter = knowledge_base_id
        self.last_limit = limit
        return self.hits[:limit]


class FakeChat:
    def __init__(self, answer: str = "项目预算为 120 万元【1】【9】。") -> None:
        self.answer = answer
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append((system_prompt, user_prompt))
        return self.answer


class FakeReranker:
    def __init__(self, scores: list[float] | None = None, error: Exception | None = None) -> None:
        self.scores = scores or []
        self.error = error
        self.calls: list[tuple[str, list[str]]] = []

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, documents))
        if self.error:
            raise self.error
        return self.scores


class RagServiceTests(unittest.TestCase):
    def test_answer_uses_sources_and_removes_invalid_citations(self) -> None:
        store = FakeStore(
            [
                {
                    "distance": 0.71,
                    "entity": {
                        "document_id": "doc-1",
                        "document_name": "设备清单.pdf",
                        "page_start": 1,
                        "page_end": 1,
                        "section_title": "项目预算表",
                        "content": "项目预算为 120 万元。",
                    },
                }
            ]
        )
        chat = FakeChat()
        service = RagService(FakeEmbedder(), store, chat, top_k=6, min_score=0.35)

        result = service.answer("项目预算是多少？", "default")

        self.assertTrue(result.grounded)
        self.assertEqual(len(result.sources), 1)
        self.assertIn("【1】", result.answer)
        self.assertNotIn("【9】", result.answer)
        self.assertEqual(store.last_filter, "default")
        self.assertIn("项目预算为 120 万元", chat.prompts[0][1])

    def test_reranker_reorders_candidates_and_preserves_vector_scores(self) -> None:
        store = FakeStore([
            {"distance": 0.91, "entity": {"document_id": "a", "document_name": "A.pdf", "page_start": 1, "content": "无关设备描述"}},
            {"distance": 0.82, "entity": {"document_id": "b", "document_name": "B.pdf", "page_start": 2, "content": "项目预算为 120 万元"}},
        ])
        reranker = FakeReranker([0.12, 0.96])
        service = RagService(FakeEmbedder(), store, FakeChat(), top_k=2, candidate_k=20, reranker=reranker)

        sources = service.retrieve("项目预算是多少？", "default")

        self.assertEqual([source.document_id for source in sources], ["b", "a"])
        self.assertEqual(sources[0].index, 1)
        self.assertEqual(sources[0].vector_score, 0.82)
        self.assertEqual(sources[0].rerank_score, 0.96)
        self.assertEqual(sources[0].score, 0.96)
        self.assertEqual(store.last_limit, 20)

    def test_reranker_failure_falls_back_to_vector_order(self) -> None:
        store = FakeStore([
            {"distance": 0.91, "entity": {"document_id": "a", "document_name": "A.pdf", "page_start": 1, "content": "第一条"}},
            {"distance": 0.82, "entity": {"document_id": "b", "document_name": "B.pdf", "page_start": 2, "content": "第二条"}},
        ])
        reranker = FakeReranker(error=RuntimeError("service unavailable"))
        service = RagService(FakeEmbedder(), store, FakeChat(), top_k=2, candidate_k=20, reranker=reranker)

        sources = service.retrieve("测试", "default")

        self.assertEqual([source.document_id for source in sources], ["a", "b"])
        self.assertTrue(all(source.rerank_score is None for source in sources))
        self.assertEqual(sources[0].score, 0.91)

    def test_low_score_returns_fixed_refusal_without_calling_chat(self) -> None:
        store = FakeStore([{"distance": 0.2, "entity": {"content": "不可靠内容"}}])
        chat = FakeChat()
        service = RagService(FakeEmbedder(), store, chat, min_score=0.35)

        result = service.answer("不存在的问题", "default")

        self.assertFalse(result.grounded)
        self.assertEqual(result.answer, "当前知识库中没有足够依据回答该问题。")
        self.assertEqual(chat.prompts, [])


if __name__ == "__main__":
    unittest.main()

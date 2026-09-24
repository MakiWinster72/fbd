from __future__ import annotations

import unittest

from knowledge_rag.milvus_store import MilvusChunkStore
from knowledge_rag.rag import RagService, _asks_document_inventory


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

    def test_inventory_question_lists_all_indexed_documents_not_only_search_hits(self) -> None:
        store = FakeStore([
            {"distance": 0.9, "entity": {"document_id": "doc-1", "document_name": "甲.docx", "content": "内容"}},
        ])
        store.list_documents = lambda kb: ["甲.docx", "乙.pdf", "丙.docx", "丁.pdf"]  # type: ignore[attr-defined]
        chat = FakeChat("只有一份材料【1】。")
        service = RagService(FakeEmbedder(), store, chat)

        result = service.answer("你只有两份材料吗？", "engineering-center")
        self.assertEqual(result.answer, "当前知识库共收录 4 份文档：\n1. 《甲.docx》\n2. 《乙.pdf》\n3. 《丙.docx》\n4. 《丁.pdf》")
        self.assertTrue(result.grounded)
        self.assertEqual(result.sources, ())
        self.assertEqual(chat.prompts, [])
        self.assertIsNone(store.last_filter)

    def test_document_inventory_intent_does_not_catch_content_question(self) -> None:
        self.assertTrue(_asks_document_inventory("你的知识库里有什么？"))
        self.assertTrue(_asks_document_inventory("有哪些文档？"))
        self.assertFalse(_asks_document_inventory("文档中有哪些重要时间节点？"))

    def test_catalog_reads_all_batches_and_deduplicates_document_ids(self) -> None:
        class Iterator:
            def __init__(self) -> None:
                self.batches = iter([
                    [{"document_id": "a", "document_name": "甲.docx"}, {"document_id": "a", "document_name": "甲.docx"}],
                    [{"document_id": "b", "document_name": "乙.pdf"}],
                    [],
                ])
                self.closed = False

            def next(self):
                return next(self.batches)

            def close(self):
                self.closed = True

        class Client:
            def __init__(self) -> None:
                self.iterator = Iterator()
                self.filter = ""

            def has_collection(self, *, collection_name):
                return True

            def query_iterator(self, *, collection_name, batch_size, filter, output_fields):
                self.filter = filter
                return self.iterator

        store = MilvusChunkStore.__new__(MilvusChunkStore)
        store.collection = "chunks"
        store.client = Client()
        self.assertEqual(store.list_documents("engineering-center"), ["乙.pdf", "甲.docx"])
        self.assertEqual(store.client.filter, 'knowledge_base_id == "engineering-center"')
        self.assertTrue(store.client.iterator.closed)
        with self.assertRaises(ValueError):
            store.list_documents('other" or true')

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

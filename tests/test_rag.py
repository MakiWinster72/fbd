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
        self.requested_files: list[tuple[str, ...]] = []

    def list_documents(self, knowledge_base_id: str) -> list[str]:
        return sorted({hit["entity"]["document_name"] for hit in self.hits})

    def search(self, vector: list[float], limit: int = 10, knowledge_base_id: str | None = None, document_names: tuple[str, ...] | None = None, page_range: tuple[int, int] | None = None) -> list[dict]:
        self.last_filter = knowledge_base_id
        self.last_limit = limit
        if document_names is not None:
            self.requested_files.append(document_names)
            return [hit for hit in self.hits if hit["entity"]["document_name"] in document_names and (page_range is None or hit["entity"].get("page_start", 1) <= page_range[1] and hit["entity"].get("page_end", hit["entity"].get("page_start", 1)) >= page_range[0])][:limit]
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


class QueryReranker(FakeReranker):
    def __init__(self, responses: dict[str, list[float]]) -> None:
        super().__init__()
        self.responses = responses

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, documents))
        return self.responses.get(query, [0.2] * len(documents))


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

    def test_independent_rerank_gate_accepts_low_vector_relevant_evidence(self) -> None:
        store = FakeStore([{"distance": 0.22, "entity": {"document_name": "A.pdf", "content": "结构化字段的真实证据"}}])
        reranker = FakeReranker([0.75])
        service = RagService(FakeEmbedder(), store, FakeChat(), min_score=0.35, reranker=reranker, rerank_min_score=0.5)
        sources = service.retrieve("《A.pdf》的字段是多少？", "default")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].vector_score, 0.22)
        self.assertEqual(sources[0].rerank_score, 0.75)
        self.assertEqual(sources[0].score, 0.75)
        self.assertEqual(service.min_score, 0.35)

    def test_independent_rerank_gate_rejects_high_vector_irrelevant_evidence(self) -> None:
        store = FakeStore([{"distance": 0.9, "entity": {"document_name": "A.pdf", "content": "无关证据"}}])
        chat = FakeChat()
        service = RagService(FakeEmbedder(), store, chat, reranker=FakeReranker([0.2]), rerank_min_score=0.5)
        self.assertFalse(service.answer("问题", "default").grounded)
        self.assertEqual(chat.prompts, [])

    def test_rerank_gate_failure_restores_original_vector_threshold(self) -> None:
        store = FakeStore([
            {"distance": 0.9, "entity": {"document_id": "a", "document_name": "A.pdf", "content": "高分证据"}},
            {"distance": 0.2, "entity": {"document_id": "b", "document_name": "B.pdf", "content": "低分证据"}},
        ])
        for scores in [[], [float("nan"), 0.9], [float("inf"), 0.9], [-0.1, 0.9], [1.1, 0.9]]:
            with self.subTest(scores=scores):
                service = RagService(FakeEmbedder(), store, FakeChat(), reranker=FakeReranker(scores), rerank_min_score=0.5)
                sources = service.retrieve("问题", "default")
                self.assertEqual([source.document_id for source in sources], ["a"])
                self.assertIsNone(sources[0].rerank_score)
                self.assertEqual(service.retrieve("根据《A.pdf》与《B.pdf》回答", "default"), ())
        service = RagService(FakeEmbedder(), store, FakeChat(), reranker=FakeReranker(error=RuntimeError("offline")), rerank_min_score=0.5)
        self.assertEqual([source.document_id for source in service.retrieve("问题", "default")], ["a"])
        self.assertEqual(service.retrieve("根据《A.pdf》与《B.pdf》回答", "default"), ())

    def test_rerank_gate_requires_all_requested_files_to_pass(self) -> None:
        store = FakeStore([
            {"distance": 0.9, "entity": {"document_name": name, "content": "正文"}}
            for name in ["A.pdf", "B.pdf"]
        ])
        service = RagService(FakeEmbedder(), store, FakeChat(), reranker=FakeReranker([0.8, 0.1]), rerank_min_score=0.5)
        self.assertEqual(service.retrieve("根据《A.pdf》与《B.pdf》回答", "default"), ())

    def test_respectively_reranks_each_file_against_its_field_and_preserves_answer_question(self) -> None:
        question = "依据《A.pdf》和《B.docx》，分别给出主管机构和金额。"
        store = FakeStore([
            {"distance": 0.2, "entity": {"document_name": "A.pdf", "content": "机构甲"}},
            {"distance": 0.9, "entity": {"document_name": "B.docx", "content": "120万元"}},
        ])
        reranker = QueryReranker({"《A.pdf》中的主管机构是什么？": [0.9], "《B.docx》中的金额是什么？": [0.8]})
        chat = FakeChat("主管机构是机构甲【1】，金额为120万元【2】。")
        service = RagService(FakeEmbedder(), store, chat, reranker=reranker, rerank_min_score=0.5)
        result = service.answer(question, "default")
        self.assertTrue(result.grounded)
        self.assertEqual(reranker.calls, [("《A.pdf》中的主管机构是什么？", ["机构甲"]), ("《B.docx》中的金额是什么？", ["120万元"])])
        self.assertEqual({source.document_name for source in result.sources}, {"A.pdf", "B.docx"})
        self.assertIn(question, chat.prompts[0][1])

    def test_respectively_mapping_keeps_mention_order_and_normalizes_file_names(self) -> None:
        store = FakeStore([
            {"distance": 0.2, "entity": {"document_name": name, "content": name}}
            for name in ["A.pdf", "B.docx"]
        ])
        reranker = QueryReranker({"《B.docx》中的金额是什么？": [0.9], "《A.pdf》中的主管机构是什么？": [0.8]})
        service = RagService(FakeEmbedder(), store, FakeChat(), reranker=reranker, rerank_min_score=0.5)
        sources = service.retrieve("依据《Ｂ．ｄｏｃｘ》和《Ａ．ｐｄｆ》，分别给出金额和主管机构。", "default")
        self.assertEqual([source.document_name for source in sources], ["B.docx", "A.pdf"])
        self.assertEqual([query for query, _ in reranker.calls], ["《B.docx》中的金额是什么？", "《A.pdf》中的主管机构是什么？"])

    def test_english_respectively_keeps_the_same_file_field_pairing(self) -> None:
        store = FakeStore([
            {"distance": 0.2, "entity": {"document_name": name, "content": name}}
            for name in ["A.pdf", "B.docx"]
        ])
        reranker = QueryReranker({"According to 《A.pdf》, what is publisher?": [0.9], "According to 《B.docx》, what is budget?": [0.8]})
        service = RagService(FakeEmbedder(), store, FakeChat(), reranker=reranker, rerank_min_score=0.5)
        sources = service.retrieve("According to 《A.pdf》 and 《B.docx》, give publisher and budget, respectively.", "default")
        self.assertEqual({source.document_name for source in sources}, {"A.pdf", "B.docx"})
        self.assertEqual([query for query, _ in reranker.calls], list(reranker.responses))

    def test_ambiguous_respectively_fields_keep_joint_query(self) -> None:
        store = FakeStore([
            {"distance": 0.9, "entity": {"document_name": name, "content": name}}
            for name in ["A.pdf", "B.docx"]
        ])
        for suffix in ["综合说明", "分别给出主管机构", "分别给出主管机构、金额和年份", "分别给出主管机构和金额；再猜测未来值", "分别给出是否获批和金额", "分别给出《B.docx》的金额和主管机构"]:
            with self.subTest(suffix=suffix):
                question = f"依据《A.pdf》和《B.docx》，{suffix}。"
                reranker = QueryReranker({question: [0.9, 0.8]})
                service = RagService(FakeEmbedder(), store, FakeChat(), reranker=reranker, rerank_min_score=0.5)
                self.assertEqual(len(service.retrieve(question, "default")), 2)
                self.assertEqual(reranker.calls, [(question, ["A.pdf", "B.docx"])])

    def test_respectively_preserves_neutral_subject_modifiers_and_units(self) -> None:
        cases = [
            ("学校的主管机构", "研发设备总值（万元）", "研发设备总值(万元)"),
            ("项目的负责人", "合同的预算(万元)", "合同的预算(万元)"),
        ]
        for first, second, normalized_second in cases:
            with self.subTest(first=first, second=second):
                question = f"依据《A.pdf》和《B.docx》，分别给出{first}和{second}。"
                store = FakeStore([{"distance": 0.2, "entity": {"document_name": name, "content": name}} for name in ["A.pdf", "B.docx"]])
                queries = [f"《A.pdf》中的{first}是什么？", f"《B.docx》中的{normalized_second}是什么？"]
                reranker = QueryReranker({query: [0.9] for query in queries})
                chat = FakeChat("机构甲【1】，总值100万元【2】。")
                result = RagService(FakeEmbedder(), store, chat, reranker=reranker, rerank_min_score=0.5).answer(question, "default")
                self.assertTrue(result.grounded)
                self.assertEqual([query for query, _ in reranker.calls], queries)
                self.assertIn(question, chat.prompts[0][1])

    def test_unit_only_or_unknown_unit_fields_do_not_trigger_score_fallback(self) -> None:
        for fields in ["(万元)和(元)", "学校的主管机构和(万元)", "预算(未定义币种)和主管机构"]:
            with self.subTest(fields=fields):
                question = f"依据《A.pdf》和《B.docx》，分别给出{fields}。"
                store = FakeStore([{"distance": 0.9, "entity": {"document_name": name, "content": name}} for name in ["A.pdf", "B.docx"]])
                reranker = QueryReranker({question: [0.2, 0.2]})
                service = RagService(FakeEmbedder(), store, FakeChat(), reranker=reranker, rerank_min_score=0.5)
                self.assertEqual(service.retrieve(question, "default"), ())
                self.assertEqual(reranker.calls, [(question, ["A.pdf", "B.docx"])])

    def test_neutral_subject_word_cannot_override_an_explicit_file_alias(self) -> None:
        question = "依据《学校.pdf》和《设备.docx》，分别给出设备的金额和学校的主管机构。"
        store = FakeStore([{"distance": 0.2, "entity": {"document_name": name, "content": name}} for name in ["学校.pdf", "设备.docx"]])
        reranker = QueryReranker({"《学校.pdf》中的设备的金额是什么？": [0.9], "《设备.docx》中的学校的主管机构是什么？": [0.9]})
        service = RagService(FakeEmbedder(), store, FakeChat(), reranker=reranker, rerank_min_score=0.5)
        self.assertEqual(service.retrieve(question, "default"), ())
        self.assertEqual(reranker.calls, [(question, ["学校.pdf", "设备.docx"])])

    def test_prompts_require_minimal_direct_citations_and_matching_identifiers(self) -> None:
        for locale, required in [("zh-CN", ["最少证据", "不要追加重复引用", "标识必须精确匹配"]), ("en", ["minimum evidence", "duplicate citations", "identifiers must match exactly"])]:
            with self.subTest(locale=locale):
                store = FakeStore([{"distance": 0.9, "entity": {"document_name": "A.pdf", "content": "项目预算120万元"}}])
                chat = FakeChat("预算为120万元【1】。")
                RagService(FakeEmbedder(), store, chat).answer("项目预算是多少？", "default", locale=locale)
                self.assertTrue(chat.prompts)
                for fragment in required:
                    self.assertIn(fragment, chat.prompts[0][0])

    def test_respectively_does_not_guess_distributive_or_explicit_file_bindings(self) -> None:
        cases = [
            ("依据《A.pdf》和《B.docx》，分别给出B的金额和A的主管机构。", ["《A.pdf》中的B的金额是什么？", "《B.docx》中的A的主管机构是什么？"]),
            ("According to 《A.pdf》 and 《B.docx》, give B's budget and A's publisher, respectively.", ["According to 《A.pdf》, what is B's budget?", "According to 《B.docx》, what is A's publisher?"]),
            ("According to 《A.pdf》 and 《B.docx》, give the budget of B and the publisher of A, respectively.", ["According to 《A.pdf》, what is the budget of B?", "According to 《B.docx》, what is the publisher of A?"]),
            ("依据《A.pdf》和《B.docx》，分别给出各自的主管机构和金额。", ["《A.pdf》中的各自的主管机构是什么？", "《B.docx》中的金额是什么？"]),
            ("依据《A.pdf》和《B.docx》，分别给出每份文件的主管机构和金额。", ["《A.pdf》中的每份文件的主管机构是什么？", "《B.docx》中的金额是什么？"]),
            ("依据《A.pdf》和《B.docx》，分别给出B.docx的金额和A.pdf的主管机构。", ["《A.pdf》中的B.docx的金额是什么？", "《B.docx》中的A.pdf的主管机构是什么？"]),
            ("依据《A.pdf》和《B.docx》，分别给出Ｂ．ｄｏｃｘ的金额和Ａ．ｐｄｆ的主管机构。", ["《A.pdf》中的B.docx的金额是什么？", "《B.docx》中的A.pdf的主管机构是什么？"]),
            ("According to 《A.pdf》 and 《B.docx》, give each file's publisher and budget, respectively.", ["According to 《A.pdf》, what is each file's publisher?", "According to 《B.docx》, what is budget?"]),
            ("According to 《A.pdf》 and 《B.docx》, give B.docx's budget and A.pdf's publisher, respectively.", ["According to 《A.pdf》, what is B.docx's budget?", "According to 《B.docx》, what is A.pdf's publisher?"]),
        ]
        for question, wrong_queries in cases:
            with self.subTest(question=question):
                store = FakeStore([{"distance": 0.2, "entity": {"document_name": name, "content": name}} for name in ["A.pdf", "B.docx"]])
                reranker = QueryReranker({query: [0.9] for query in wrong_queries})
                chat = FakeChat()
                result = RagService(FakeEmbedder(), store, chat, reranker=reranker, rerank_min_score=0.5).answer(question, "default")
                self.assertFalse(result.grounded)
                self.assertEqual(result.sources, ())
                self.assertEqual(chat.prompts, [])
                self.assertEqual(reranker.calls, [(question, ["A.pdf", "B.docx"])])

    def test_respectively_does_not_split_conjunctions_inside_proper_names(self) -> None:
        cases = [
            ("According to 《A.pdf》 and 《B.docx》, give the Research and Development Department, respectively.", ["According to 《A.pdf》, what is the Research?", "According to 《B.docx》, what is Development Department?"]),
            ("依据《A.pdf》和《B.docx》，分别给出教育机构和实验中心的共建日期。", ["《A.pdf》中的教育机构是什么？", "《B.docx》中的实验中心的共建日期是什么？"]),
            ("依据《A.pdf》和《B.docx》，分别给出中华人民共和国教育部的批复日期。", ["《A.pdf》中的中华人民共是什么？", "《B.docx》中的国教育部的批复日期是什么？"]),
            ("依据《A.pdf》和《B.docx》，分别给出天和公司的批复日期。", ["《A.pdf》中的天是什么？", "《B.docx》中的公司的批复日期是什么？"]),
            ("依据《A.pdf》和《B.docx》，分别给出和田公司的批复日期。", ["《A.pdf》中的田公司的批复日期是什么？", "《B.docx》中的批复日期是什么？"]),
        ]
        for question, wrong_queries in cases:
            with self.subTest(question=question):
                store = FakeStore([{"distance": 0.2, "entity": {"document_name": name, "content": name}} for name in ["A.pdf", "B.docx"]])
                reranker = QueryReranker({query: [0.9] for query in wrong_queries})
                chat = FakeChat()
                result = RagService(FakeEmbedder(), store, chat, reranker=reranker, rerank_min_score=0.5).answer(question, "default")
                self.assertFalse(result.grounded)
                self.assertEqual(chat.prompts, [])
                self.assertEqual(reranker.calls, [(question, ["A.pdf", "B.docx"])])

    def test_respectively_scores_stay_aligned_with_interleaved_candidates(self) -> None:
        store = FakeStore([
            {"distance": 0.2, "entity": {"document_id": "a", "document_name": "A.pdf", "content": "机构甲"}},
            {"distance": 0.9, "entity": {"document_id": "b1", "document_name": "B.docx", "content": "120万元"}},
            {"distance": 0.8, "entity": {"document_id": "b2", "document_name": "B.docx", "content": "无关内容"}},
        ])
        reranker = QueryReranker({"《A.pdf》中的主管机构是什么？": [0.7], "《B.docx》中的金额是什么？": [0.9, 0.1]})
        service = RagService(FakeEmbedder(), store, FakeChat(), reranker=reranker, rerank_min_score=0.5)
        sources = service.retrieve("依据《A.pdf》和《B.docx》，分别给出主管机构和金额。", "default")
        self.assertEqual([(source.document_id, source.rerank_score) for source in sources], [("b1", 0.9), ("a", 0.7)])
        self.assertEqual(reranker.calls[1][1], ["120万元", "无关内容"])

    def test_respectively_batch_failure_keeps_whole_request_vector_fallback(self) -> None:
        store = FakeStore([
            {"distance": 0.2, "entity": {"document_name": "A.pdf", "content": "机构甲"}},
            {"distance": 0.9, "entity": {"document_name": "B.docx", "content": "120万元"}},
        ])
        for bad in [[], [float("nan")], [1.1]]:
            with self.subTest(bad=bad):
                reranker = QueryReranker({"《A.pdf》中的主管机构是什么？": [0.9], "《B.docx》中的金额是什么？": bad})
                service = RagService(FakeEmbedder(), store, FakeChat(), reranker=reranker, rerank_min_score=0.5)
                self.assertEqual(service.retrieve("依据《A.pdf》和《B.docx》，分别给出主管机构和金额。", "default"), ())
                self.assertEqual(len(reranker.calls), 2)

    def test_respectively_still_requires_each_file_to_pass_relevance_gate(self) -> None:
        store = FakeStore([
            {"distance": 0.9, "entity": {"document_name": name, "content": name}}
            for name in ["A.pdf", "B.docx"]
        ])
        reranker = QueryReranker({"《A.pdf》中的主管机构是什么？": [0.9], "《B.docx》中的金额是什么？": [0.1]})
        service = RagService(FakeEmbedder(), store, FakeChat(), reranker=reranker, rerank_min_score=0.5)
        self.assertEqual(service.retrieve("依据《A.pdf》和《B.docx》，分别给出主管机构和金额。", "default"), ())
        self.assertEqual(len(reranker.calls), 2)

    def test_rerank_gate_without_reranker_keeps_vector_refusal(self) -> None:
        store = FakeStore([{"distance": 0.2, "entity": {"content": "低分证据"}}])
        service = RagService(FakeEmbedder(), store, FakeChat(), rerank_min_score=0.5)
        self.assertEqual(service.retrieve("问题", "default"), ())

    def test_non_finite_vector_scores_never_reach_reranker(self) -> None:
        for score in [float("nan"), float("inf")]:
            with self.subTest(score=score):
                store = FakeStore([{"distance": score, "entity": {"document_name": "A.pdf", "content": "正文"}}])
                reranker = FakeReranker([0.9])
                service = RagService(FakeEmbedder(), store, FakeChat(), reranker=reranker, rerank_min_score=0.5)
                self.assertEqual(service.retrieve("问题", "default"), ())
                self.assertEqual(reranker.calls, [])

    def test_invalid_rerank_threshold_is_rejected(self) -> None:
        for threshold in [0.0, -0.1, 1.1, float("nan"), float("inf")]:
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                RagService(FakeEmbedder(), FakeStore([]), FakeChat(), rerank_min_score=threshold)

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
        client = Client()
        store.__dict__["client"] = client
        self.assertEqual(store.list_documents("engineering-center"), ["乙.pdf", "甲.docx"])
        self.assertEqual(client.filter, 'knowledge_base_id == "engineering-center"')
        self.assertTrue(client.iterator.closed)
        with self.assertRaises(ValueError):
            store.list_documents('other" or true')

    def test_english_response_prompt_inventory_and_refusal(self) -> None:
        store = FakeStore([{"distance": 0.9, "entity": {"document_id": "a", "document_name": "A.pdf", "page_start": 1, "content": "预算为 120 万元"}}])
        chat = FakeChat("The budget is 1.2 million yuan【1】.")
        service = RagService(FakeEmbedder(), store, chat)
        result = service.answer("What is the budget?", "default", "en")
        self.assertIn("Answer in English", chat.prompts[0][0])
        self.assertEqual(result.answer, "The budget is 1.2 million yuan【1】.")
        store.list_documents = lambda kb: ["A.pdf", "B.pdf"]  # type: ignore[attr-defined]
        self.assertIn("2 documents", service.answer("List the documents", "default", "en").answer)
        store.hits = []
        self.assertIn("not enough evidence", service.answer("Unknown question", "default", "en").answer)

    def test_model_refusal_and_uncited_answer_are_not_grounded(self) -> None:
        hits = [{"distance": 0.9, "entity": {"document_id": "a", "document_name": "A.pdf", "content": "预算为 120 万元"}}]
        for answer, locale in [
            ("当前知识库中没有足够依据回答该问题。", "zh-CN"),
            ("当前知识库中没有足够依据回答该问题。【1】", "zh-CN"),
            ("There is not enough evidence in the current knowledge base to answer this question.【1】", "en"),
            ("未经证据支持的结论【9】", "zh-CN"),
            ("An unsupported factual answer", "en"),
            ("", "zh-CN"),
        ]:
            with self.subTest(answer=answer):
                result = RagService(FakeEmbedder(), FakeStore(hits), FakeChat(answer)).answer("未知问题", "default", locale)
                self.assertFalse(result.grounded)
                self.assertEqual(result.sources, ())
                self.assertIn("not enough evidence" if locale == "en" else "没有足够依据", result.answer)

    def test_explicit_file_is_filtered_before_vector_top_k(self) -> None:
        store = FakeStore([
            {"distance": 0.99, "entity": {"document_id": "wrong", "document_name": "其他资料.pdf", "content": "无关说明"}},
            {"distance": 0.68, "entity": {"document_id": "wanted", "document_name": "学校法人证（新）.pdf", "content": "举办单位：某教育机构"}},
        ])
        service = RagService(FakeEmbedder(), store, FakeChat(), top_k=1)
        for question in ["《学校法人证（新）.pdf》中的举办单位？", "According to 学校法人证(新).PDF, who is the founder?", "《学校法人证（新）》中的举办单位？"]:
            with self.subTest(question=question):
                sources = service.retrieve(question, "default")
                self.assertEqual([source.document_id for source in sources], ["wanted"])
                self.assertEqual(store.requested_files[-1], ("学校法人证（新）.pdf",))
                self.assertEqual(store.last_filter, "default")

    def test_multiple_requested_files_each_get_candidate_coverage(self) -> None:
        store = FakeStore([
            {"distance": 0.99 - index * 0.01, "entity": {"document_id": "a", "document_name": "A.pdf", "content": "第一份文件"}}
            for index in range(6)
        ] + [{"distance": 0.55, "entity": {"document_id": "b", "document_name": "B.docx", "content": "第二份文件"}}])
        sources = RagService(FakeEmbedder(), store, FakeChat(), top_k=2).retrieve("综合《A.pdf》和《B.docx》说明", "default")
        self.assertEqual({source.document_id for source in sources}, {"a", "b"})
        self.assertEqual([source.index for source in sources], [1, 2])

    def test_missing_explicit_file_does_not_answer_from_other_documents(self) -> None:
        store = FakeStore([{"distance": 0.99, "entity": {"document_id": "a", "document_name": "A.pdf", "content": "其他内容"}}])
        chat = FakeChat()
        service = RagService(FakeEmbedder(), store, chat)
        for question in ["《缺失.pdf》中有哪些内容？", "According to missing.pdf, what is the budget?", "根据《A.pdf》和《缺失.pdf》说明"]:
            with self.subTest(question=question):
                result = service.answer(question, "default")
                self.assertFalse(result.grounded)
                self.assertEqual(result.sources, ())
        self.assertEqual(chat.prompts, [])
        self.assertIsNone(store.last_filter)

    def test_reranking_cannot_starve_an_explicitly_requested_file(self) -> None:
        store = FakeStore([
            {"distance": 0.9, "entity": {"document_id": "a1", "document_name": "Annual report.pdf", "content": "首条证据"}},
            {"distance": 0.85, "entity": {"document_id": "a2", "document_name": "Annual report.pdf", "content": "第二条证据"}},
            {"distance": 0.7, "entity": {"document_id": "b", "document_name": "B.docx", "content": "另一文件"}},
        ])
        reranker = FakeReranker([0.99, 0.1, 0.95])
        service = RagService(FakeEmbedder(), store, FakeChat(), top_k=2, reranker=reranker)
        sources = service.retrieve('Compare "Annual report.pdf" and "B.docx"', "default")
        self.assertEqual([source.document_id for source in sources], ["a1", "b"])
        self.assertEqual([source.rerank_score for source in sources], [0.99, 0.1])

    def test_insufficient_score_in_requested_file_refuses_instead_of_using_another(self) -> None:
        store = FakeStore([
            {"distance": 0.99, "entity": {"document_id": "a", "document_name": "A.pdf", "content": "其他内容"}},
            {"distance": 0.2, "entity": {"document_id": "b", "document_name": "B.pdf", "content": "低分证据"}},
        ])
        chat = FakeChat()
        result = RagService(FakeEmbedder(), store, chat).answer("根据《A.pdf》和《B.pdf》回答", "default")
        self.assertFalse(result.grounded)
        self.assertEqual(result.sources, ())
        self.assertEqual(chat.prompts, [])

    def test_content_question_with_named_file_is_not_global_inventory(self) -> None:
        self.assertFalse(_asks_document_inventory("《申报材料.pdf》中有哪些文档目录？"))

    def test_named_page_filter_is_applied_before_vector_top_k(self) -> None:
        store = FakeStore([
            {"distance": 0.99, "entity": {"document_id": "a", "document_name": "A.pdf", "page_start": 23, "page_end": 24, "content": "相似但不是指定页的项目"}},
            {"distance": 0.71, "entity": {"document_id": "b", "document_name": "A.pdf", "page_start": 1, "page_end": 2, "content": "指定页的项目结项日期"}},
        ])
        service = RagService(FakeEmbedder(), store, FakeChat(), top_k=1)
        for question in ["根据《A.pdf》第2页回答", "According to A.pdf, on page 2, when did it finish?", "根据《A.pdf》第2至3页回答"]:
            with self.subTest(question=question):
                sources = service.retrieve(question, "default")
                self.assertEqual([source.document_id for source in sources], ["b"])

    def test_named_page_scope_rejects_invalid_or_missing_pages(self) -> None:
        store = FakeStore([{"distance": 0.99, "entity": {"document_name": "A.pdf", "page_start": 1, "page_end": 2, "content": "正文"}}])
        service = RagService(FakeEmbedder(), store, FakeChat())
        for question in ["《A.pdf》第0页", "《A.pdf》第3至2页", "《A.pdf》第99页", "《A.pdf》第1页与第2页分别说什么"]:
            with self.subTest(question=question):
                self.assertEqual(service.retrieve(question, "default"), ())

    def test_page_word_inside_filename_does_not_filter_pages(self) -> None:
        store = FakeStore([{"distance": 0.8, "entity": {"document_name": "第2页报告.pdf", "page_start": 9, "page_end": 9, "content": "正文"}}])
        service = RagService(FakeEmbedder(), store, FakeChat())
        self.assertEqual(len(service.retrieve("《第2页报告.pdf》有哪些内容", "default")), 1)

    def test_multiple_files_with_page_selectors_refuse_instead_of_guessing(self) -> None:
        store = FakeStore([
            {"distance": 0.9, "entity": {"document_name": name, "page_start": 5, "page_end": 5, "content": "正文"}}
            for name in ["A.pdf", "B.pdf"]
        ])
        service = RagService(FakeEmbedder(), store, FakeChat())
        self.assertEqual(service.retrieve("《A.pdf》第1页与《B.pdf》第2页有何不同？", "default"), ())

    def test_milvus_document_scope_preserves_kb_filter_and_escapes_names(self) -> None:
        class Client:
            def search(self, **kwargs):
                self.expression = kwargs["filter"]
                return [[]]
        store = MilvusChunkStore.__new__(MilvusChunkStore)
        store.collection = "chunks"
        client = Client()
        store.__dict__["client"] = client
        name = 'A" or true.pdf'
        self.assertEqual(store.search([0.1], knowledge_base_id="private", document_names=(name,)), [])
        self.assertIn('knowledge_base_id == "private"', client.expression)
        self.assertIn('document_name in ["A\\\" or true.pdf"]', client.expression)
        self.assertEqual(store.search([0.1], knowledge_base_id="private", document_names=(name,), page_range=(2, 3)), [])
        self.assertIn('knowledge_base_id == "private"', client.expression)
        self.assertIn('document_name in ["A\\\" or true.pdf"]', client.expression)
        self.assertIn("page_start <= 3 and page_end >= 2", client.expression)
        for invalid in [(0, 1), (3, 2), (True, 3), (1, 2**63)]:
            with self.subTest(page_range=invalid), self.assertRaises(ValueError):
                store.search([0.1], page_range=invalid)
        self.assertEqual(store.search([0.1], knowledge_base_id="private", document_names=()), [])
        with self.assertRaises(ValueError):
            store.search([0.1], knowledge_base_id='private" or true', document_names=("A.pdf",))

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

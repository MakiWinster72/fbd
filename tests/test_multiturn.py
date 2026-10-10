from __future__ import annotations

import json
import unittest
from unittest.mock import Mock

from knowledge_rag.conversation_context import (
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_TURNS,
    HistoryTurn,
    bounded_history,
    resolve_question,
)
from knowledge_rag.rag import (
    REFUSAL_EN,
    REFUSAL_ZH,
    RagService,
    _asks_document_inventory,
)


def make_service(responses: list[str], *, hits: bool = True):
    embedder = Mock()
    embedder.embed.return_value = [[0.1]]
    store = Mock()
    store.list_documents.return_value = ["预算.pdf", "人员.pdf"]
    store.search.return_value = [{"distance": 0.9, "entity": {
        "document_id": "budget", "document_name": "预算.pdf", "page_start": 2, "page_end": 2,
        "content": "项目预算120万元，负责人张三。", "section_title": "预算",
    }}] if hits else []
    model = Mock()
    model.complete.side_effect = responses
    return RagService(embedder, store, model), embedder, store, model


class MultiTurnTests(unittest.TestCase):
    def test_recent_suffix_has_round_and_character_budgets(self) -> None:
        turns = [HistoryTurn(str(i) + "问" * 3000, str(i) + "答" * 3000) for i in range(10)]
        recent = bounded_history(turns)
        self.assertEqual(len(recent), 4)
        self.assertEqual([turn.question[0] for turn in recent], ["6", "7", "8", "9"])
        self.assertEqual(sum(len(t.question) + len(t.answer) for t in recent), MAX_CONTEXT_CHARS)
        self.assertEqual(len(bounded_history([HistoryTurn(str(i), "") for i in range(20)])), MAX_CONTEXT_TURNS)

    def test_no_history_is_compatible_and_does_not_rewrite(self) -> None:
        service, embedder, _, model = make_service(["预算120万元【1】"])
        self.assertTrue(service.answer("预算是什么？", "default").grounded)
        embedder.embed.assert_called_once_with(["预算是什么？"])
        self.assertEqual(model.complete.call_count, 1)

    def test_followup_is_resolved_before_retrieval_and_history_is_not_evidence(self) -> None:
        standalone = "《预算.pdf》第2页的项目负责人是谁？"
        service, embedder, store, model = make_service([json.dumps({"question": standalone}), "负责人张三【1】【9】"])
        history = (HistoryTurn("《预算.pdf》第2页预算多少？", "PRIVATE OLD ANSWER【9】"),)
        result = service.answer("那负责人呢？", "default", "zh-CN", history)
        self.assertTrue(result.grounded)
        self.assertNotIn("【9】", result.answer)
        embedder.embed.assert_called_once_with([standalone])
        self.assertEqual(store.search.call_args.kwargs["document_names"], ("预算.pdf",))
        self.assertEqual(store.search.call_args.kwargs["page_range"], (2, 2))
        resolver_payload = json.loads(model.complete.call_args_list[0].args[1])
        self.assertEqual(resolver_payload["history"][0]["answer"], history[0].answer)
        final_prompt = model.complete.call_args_list[1].args[1]
        self.assertIn("那负责人呢？", final_prompt)
        self.assertNotIn("PRIVATE OLD ANSWER", final_prompt)
        self.assertIn("项目预算120万元，负责人张三", final_prompt)

    def test_history_cannot_bypass_empty_evidence_or_locale(self) -> None:
        for locale, refusal in [("zh-CN", REFUSAL_ZH), ("en", REFUSAL_EN)]:
            with self.subTest(locale=locale):
                service, _, _, model = make_service(['{"question":"项目负责人是谁？"}'], hits=False)
                result = service.answer("那负责人呢？", "default", locale, (HistoryTurn("项目？", "张三【1】"),))
                self.assertEqual(result.answer, refusal)
                self.assertFalse(result.grounded)
                self.assertEqual(result.sources, ())
                self.assertEqual(model.complete.call_count, 1)

    def test_bad_or_ambiguous_rewrite_clarifies_without_search(self) -> None:
        for raw in ["plain answer", "null", "[]", '{"question":null}', '{"question":true}', '{"question":""}', '{"question":"answer【1】"}', '{"question":"valid", "extra":1}', json.dumps({"question":"x" * 2001}), "x" * 16001]:
            with self.subTest(raw=raw[:50]):
                service, embedder, store, _ = make_service([raw])
                result = service.answer("What about it?", "default", "en", (HistoryTurn("Two projects", "A and B"),))
                self.assertFalse(result.grounded)
                self.assertIn("Please specify", result.answer)
                self.assertEqual(result.sources, ())
                embedder.embed.assert_not_called()
                store.search.assert_not_called()

    def test_resolver_accepts_fenced_json_but_not_trailing_prose(self) -> None:
        complete = Mock(return_value='```json\n{"question":"项目负责人？"}\n```')
        self.assertEqual(resolve_question("他呢？", (HistoryTurn("项目", ""),), complete), "项目负责人？")
        complete.return_value += "more words"
        self.assertIsNone(resolve_question("他呢？", (HistoryTurn("项目", ""),), complete))

    def test_current_file_and_page_constraints_cannot_be_broadened(self) -> None:
        cases = [
            ("《预算.pdf》第2页负责人？", "项目负责人？"),
            ("《预算.pdf》第2页负责人？", "《人员.pdf》第2页负责人？"),
            ("《预算.pdf》第2页负责人？", "《预算.pdf》第3页负责人？"),
            ("《预算.pdf》负责人？", "《预算.pdf》和《人员.pdf》负责人？"),
            ("第2页负责人？", "《预算.pdf》负责人？"),
            ("《未知.pdf》负责人？", "《预算.pdf》负责人？"),
            ("第2页负责人？", "《未知.pdf》第2页负责人？"),
            ("第2页负责人？", "《预算.pdf》和《人员.pdf》第2页负责人？"),
            ("负责人呢？", "预算项目第2页负责人？"),
            ("第0页负责人？", "《预算.pdf》第0页负责人？"),
            ("第2至1页负责人？", "《预算.pdf》第2至1页负责人？"),
        ]
        for question, resolved in cases:
            with self.subTest(question=question, resolved=resolved):
                service, embedder, _, _ = make_service([json.dumps({"question": resolved})])
                result = service.answer(question, "default", history=(HistoryTurn("旧问题", ""),))
                self.assertFalse(result.grounded)
                self.assertIn("请明确", result.answer)
                embedder.embed.assert_not_called()

    def test_page_followup_without_resolved_document_must_clarify(self) -> None:
        service, embedder, store, _ = make_service([
            '{"question":"预算项目第3页负责人是谁？"}', "负责人张三【1】",
        ])
        result = service.answer("第3页负责人？", "default", history=(HistoryTurn("《预算.pdf》预算多少？", "120万元【1】"),))
        self.assertFalse(result.grounded)
        self.assertIn("请明确", result.answer)
        self.assertEqual(result.sources, ())
        embedder.embed.assert_not_called()
        store.search.assert_not_called()

    def test_explicit_page_followup_reaches_document_and_page_filters(self) -> None:
        resolved = "《预算.pdf》第2页负责人是谁？"
        service, _, store, _ = make_service([json.dumps({"question": resolved}), "负责人张三【1】"])
        result = service.answer("第2页负责人？", "default", history=(HistoryTurn("《预算.pdf》预算多少？", "120万元【1】"),))
        self.assertTrue(result.grounded)
        self.assertEqual(store.search.call_args.kwargs["document_names"], ("预算.pdf",))
        self.assertEqual(store.search.call_args.kwargs["page_range"], (2, 2))

    def test_retrieve_never_silently_ignores_unscoped_pages(self) -> None:
        service, embedder, store, _ = make_service([])
        self.assertEqual(service.retrieve("第3页负责人？", "default"), ())
        embedder.embed.assert_not_called()
        store.search.assert_not_called()

    def test_second_document_content_followup_is_not_inventory(self) -> None:
        standalone = "What is the budget in 《预算.pdf》?"
        service, embedder, _, model = make_service([json.dumps({"question": standalone}), "The budget is 1.2 million.【1】"])
        result = service.answer("What is the budget in the second document?", "default", "en", (
            HistoryTurn("List the documents", "1. 《人员.pdf》\n2. 《预算.pdf》"),
        ))
        self.assertEqual(result.answer, "The budget is 1.2 million.【1】")
        self.assertEqual(model.complete.call_count, 2)
        embedder.embed.assert_called_once_with([standalone])

    def test_only_explicit_inventory_queries_take_shortcut(self) -> None:
        positives = ["List the documents", "What documents are there?", "Which files are in this knowledge base?", "How many documents are indexed?", "Documents in the archive", "Please show me all files", "知识库有哪些文档？", "你的知识库里有什么？", "你只有两份材料吗？", "有哪些文档？", "文档清单"]
        negatives = ["What is the budget in the second document?", "Which documents mention the budget?", "List the budgets in the documents", "How many projects are in the files?", "What do the documents in the archive say about budgets?", "知识库第二份文档的预算是多少？", "文档中有哪些重要时间节点？", "列出第二份文档中的项目", "有哪些文档提到预算？"]
        for question in positives:
            with self.subTest(question=question):
                self.assertTrue(_asks_document_inventory(question))
        for question in negatives:
            with self.subTest(question=question):
                self.assertFalse(_asks_document_inventory(question))

    def test_new_topic_and_locale_use_current_request(self) -> None:
        question = "What is the budget in 《预算.pdf》 on page 2?"
        service, embedder, store, model = make_service([json.dumps({"question": question}), "The budget is 1.2 million.【1】"])
        result = service.answer(question, "default", "en", (HistoryTurn("《人员.pdf》人员是谁？", "旧答案"),))
        self.assertTrue(result.grounded)
        embedder.embed.assert_called_once_with([question])
        self.assertEqual(store.search.call_args.kwargs["document_names"], ("预算.pdf",))
        self.assertIn("Answer in English", model.complete.call_args_list[-1].args[0])
        self.assertNotIn("旧答案", model.complete.call_args_list[-1].args[1])

    def test_inventory_does_not_need_rewrite_or_old_answers(self) -> None:
        service, embedder, _, model = make_service([])
        result = service.answer("知识库有哪些文档？", "default", history=(HistoryTurn("项目？", "错的清单"),))
        self.assertTrue(result.grounded)
        self.assertIn("2 份", result.answer)
        embedder.embed.assert_not_called()
        model.complete.assert_not_called()

    def test_resolver_provider_failure_is_not_silently_a_standalone_question(self) -> None:
        service, embedder, _, model = make_service([])
        model.complete.side_effect = RuntimeError("provider down")
        with self.assertRaisesRegex(RuntimeError, "provider down"):
            service.answer("它呢？", "default", history=(HistoryTurn("项目", ""),))
        embedder.embed.assert_not_called()

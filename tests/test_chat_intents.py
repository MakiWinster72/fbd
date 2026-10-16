import json
import unittest
from unittest.mock import Mock

from knowledge_rag.conversation_context import HistoryTurn
from knowledge_rag.rag import REFUSAL_ZH, RagService


def make_service() -> tuple[RagService, Mock, Mock, Mock]:
    embedder = Mock(spec=["embed"])
    embedder.embed.return_value = [[0.1, 0.2]]
    store = Mock(spec=["search", "list_documents"])
    store.search.return_value = []
    store.list_documents.return_value = []
    model = Mock(spec=["complete"])
    model.complete.side_effect = AssertionError("A greeting must not call the model")
    return RagService(embedder=embedder, store=store, chat_model=model), embedder, store, model


class GreetingIntentTests(unittest.TestCase):
    def test_greeting_explains_capabilities_without_retrieval(self) -> None:
        for locale, expected in [("zh-CN", "你好"), ("en", "Hello")]:
            for question in ["hi", "Hi!", "你好！", " hello? "]:
                for history in [(), (HistoryTurn("采购了什么设备", ""),)]:
                    with self.subTest(locale=locale, question=question, history=bool(history)):
                        service, embedder, store, model = make_service()
                        result = service.answer(question, "current-kb", locale, history)
                        self.assertIn(expected, result.answer)
                        self.assertFalse(result.grounded)
                        self.assertEqual(result.sources, ())
                        embedder.embed.assert_not_called()
                        store.search.assert_not_called()
                        model.complete.assert_not_called()

    def test_explicit_knowledge_base_overview_does_not_depend_on_history_rewrite(self) -> None:
        for locale, question in [("zh-CN", "知识库中写了什么"), ("en", "What is in this knowledge base?")]:
            with self.subTest(locale=locale):
                service, embedder, store, model = make_service()
                store.search.return_value = [{"distance": 0.7, "entity": {"content": "资料列出服务器。", "document_name": "设备清单.pdf"}}]
                answer = "资料列出了服务器。【1】"
                model.complete.side_effect = [answer, json.dumps({"claims": [{"text": answer, "verdict": "supported", "evidence": [{"index": 1, "quote": "资料列出服务器。"}]}]})]
                result = service.answer(question, "current-kb", locale, history=(HistoryTurn("hi", ""),))
                self.assertTrue(result.grounded)
                self.assertIn("【1】", result.answer)
                embedder.embed.assert_called_once_with([question])
                self.assertEqual(model.complete.call_count, 2)
                self.assertIn("检索证据", model.complete.call_args_list[0].args[1])
                if locale == "en":
                    self.assertIn("Answer in English", model.complete.call_args_list[0].args[0])

    def test_greeting_word_inside_a_factual_question_does_not_bypass_retrieval(self) -> None:
        service, embedder, _, _ = make_service()
        result = service.answer("采购了 hi 型设备吗？", "current-kb")
        self.assertEqual(result.answer, REFUSAL_ZH)
        embedder.embed.assert_called_once()

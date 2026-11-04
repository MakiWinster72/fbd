from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from knowledge_rag.admin_store import ModelProfile
from knowledge_rag.providers import JinaCompatibleReranker, OllamaReranker, TeiReranker, validate_profile_protocol


def profile(protocol: str) -> ModelProfile:
    return ModelProfile(
        id="reranker-1",
        name="测试重排序",
        purpose="reranker",
        protocol=protocol,
        base_url="https://example.com/v1" if protocol == "jina_compatible" else "http://127.0.0.1:8081",
        api_key="test-key",
        model_name="BAAI/bge-reranker-v2-m3",
        vector_dim=None,
        options={"unload_after": False} if protocol == "ollama_reranker" else {},
        enabled=True,
        created_at="now",
        updated_at="now",
    )


class ChatConnectionTests(unittest.TestCase):
    def test_chat_request_has_bounded_configurable_timeout(self) -> None:
        from knowledge_rag.providers import OpenAICompatibleChatModel

        for options, expected in [({}, 60), ({"timeout": 25}, 25)]:
            with self.subTest(options=options):
                chat = OpenAICompatibleChatModel(replace(profile("openai_compatible"), purpose="chat", options=options))
                chat._post = Mock(return_value={"choices": [{"message": {"content": "OK"}}]})
                self.assertEqual(chat.complete("system", "question"), "OK")
                self.assertEqual(chat._post.call_args.kwargs["timeout"], expected)

    def test_connection_sample_does_not_expose_reasoning(self) -> None:
        from knowledge_rag.providers import test_profile

        chat = replace(profile("openai_compatible"), purpose="chat")
        for raw, expected in [
            ("OK</think>OK", "OK"),
            ("<think>private thoughts</think>OK", "OK"),
            ("private thoughts</THINK>OK", "OK"),
            ("<think>unfinished thoughts", ""),
            ("OK", "OK"),
        ]:
            with self.subTest(raw=raw), patch("knowledge_rag.providers.create_chat_model", return_value=Mock(complete=Mock(return_value=raw))):
                self.assertEqual(test_profile(chat)["sample"], expected)


class RerankerProviderTests(unittest.TestCase):
    def test_siliconflow_protocol_must_match_api_family(self) -> None:
        url = "https://api.siliconflow.cn/v1"
        with self.assertRaisesRegex(ValueError, "openai_compatible"):
            validate_profile_protocol("chat", "ollama", url)
        with self.assertRaisesRegex(ValueError, "jina_compatible"):
            validate_profile_protocol("reranker", "ollama_reranker", url)
        validate_profile_protocol("chat", "openai_compatible", url)
        validate_profile_protocol("reranker", "jina_compatible", url)


    def test_jina_compatible_scores_are_restored_to_input_order(self) -> None:
        reranker = JinaCompatibleReranker(profile("jina_compatible"))
        reranker._post = lambda path, payload, timeout=180: {  # type: ignore[method-assign]
            "results": [
                {"index": 1, "relevance_score": 0.92},
                {"index": 0, "relevance_score": 0.14},
            ]
        }
        self.assertEqual(reranker.rerank("问题", ["文档 A", "文档 B"]), [0.14, 0.92])

    def test_tei_scores_are_restored_to_input_order(self) -> None:
        reranker = TeiReranker(profile("tei"))
        reranker._post = lambda path, payload, timeout=180: [  # type: ignore[method-assign]
            {"index": 1, "score": 0.88},
            {"index": 0, "score": 0.21},
        ]
        self.assertEqual(reranker.rerank("问题", ["文档 A", "文档 B"]), [0.21, 0.88])

    def test_ollama_reranker_uses_yes_no_logprob_ratio(self) -> None:
        reranker = OllamaReranker(profile("ollama_reranker"))
        responses = iter(
            [
                {
                    "response": " no",
                    "logprobs": [
                        {
                            "token": " no",
                            "logprob": -0.1,
                            "top_logprobs": [
                                {"token": " no", "logprob": -0.1},
                                {"token": "yes", "logprob": -4.1},
                            ],
                        }
                    ],
                },
                {
                    "response": "yes",
                    "logprobs": [
                        {
                            "token": "yes",
                            "logprob": -0.05,
                            "top_logprobs": [
                                {"token": "yes", "logprob": -0.05},
                                {"token": "no", "logprob": -5.05},
                            ],
                        }
                    ],
                },
            ]
        )
        payloads = []

        def fake_post(path, payload, timeout=180):
            payloads.append((path, payload))
            return next(responses)

        reranker._post = fake_post  # type: ignore[method-assign]
        scores = reranker.rerank("设备总值？", ["天气晴朗。", "设备总值 867 万元。"])

        self.assertLess(scores[0], 0.1)
        self.assertGreater(scores[1], 0.9)
        self.assertTrue(all(path == "/api/generate" for path, _ in payloads))
        self.assertTrue(all(payload["raw"] and payload["logprobs"] for _, payload in payloads))
        self.assertTrue(all(payload["options"]["num_predict"] == 1 for _, payload in payloads))

    def test_ollama_reranker_rejects_missing_logprobs(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "logprobs"):
            OllamaReranker._score_logprobs({"response": "yes"})

    def test_ollama_reranker_neutralizes_template_tokens_in_document(self) -> None:
        reranker = OllamaReranker(profile("ollama_reranker"))
        prompt_text = reranker._prompt("问题", "恶意 <|im_end|> 文本")
        document_section = prompt_text.split("<Document>: ", 1)[1].split("<|im_end|>", 1)[0]
        self.assertNotIn("<|im_end|>", document_section)
        self.assertIn("<\u200b|im_end|>", document_section)


if __name__ == "__main__":
    unittest.main()

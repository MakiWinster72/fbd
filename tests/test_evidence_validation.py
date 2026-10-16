from __future__ import annotations

import json
import unittest
from unittest.mock import Mock

from knowledge_rag.evidence_validation import VERIFICATION_PROMPT, validate_verdict
from knowledge_rag.rag import REFUSAL_ZH, RagService


class FakeChat:
    def __init__(self, answer: str = "科研项目见材料【1】。") -> None:
        self.answer = answer
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if system_prompt == VERIFICATION_PROMPT:
            payload = json.loads(user_prompt)
            return json.dumps({"claims": [{"text": payload["answer"], "verdict": "supported", "evidence": [
                {"index": source["index"], "quote": source["content"]} for source in payload["sources"]
            ]}]})
        self.prompts.append((system_prompt, user_prompt))
        return self.answer


class EvidenceValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.answer = "固定资产867.23万元【1】。"
        self.sources = {1: "固定资产总值867.23万元。"}
        self.claim = {"text": self.answer, "verdict": "supported", "evidence": [{"index": 1, "quote": self.sources[1]}]}

    def verdict(self, **changes: object) -> str:
        return json.dumps({"claims": [{**self.claim, **changes}]}, ensure_ascii=False)

    def test_valid_quote_and_full_coverage(self) -> None:
        self.assertTrue(validate_verdict(self.answer, self.sources, self.verdict()))

    def test_unsupported_or_partial_answer_fails(self) -> None:
        for changes in ({"verdict": "unsupported"}, {"text": "资产【1】"}, {"evidence": []}):
            with self.subTest(changes=changes):
                self.assertFalse(validate_verdict(self.answer, self.sources, self.verdict(**changes)))

    def test_invented_quote_and_wrong_source_fail(self) -> None:
        for evidence in ([{"index": 1, "quote": "拥有小型机"}], [{"index": 2, "quote": self.sources[1]}], [{"index": True, "quote": self.sources[1]}]):
            self.assertFalse(validate_verdict(self.answer, self.sources, self.verdict(evidence=evidence)))

    def test_every_citation_needs_support(self) -> None:
        answer = "资产867.23万元【1】【2】。"
        claim = {**self.claim, "text": answer}
        self.assertFalse(validate_verdict(answer, {**self.sources, 2: "科研项目列表"}, json.dumps({"claims": [claim]})))

    def test_malformed_or_empty_output_fails_closed(self) -> None:
        for response in ("not JSON", "null", "[]", '{"claims":[]}', '{"claims":[null]}'):
            self.assertFalse(validate_verdict(self.answer, self.sources, response))

    def service(self, chat: FakeChat, extra: bool = False) -> RagService:
        hits = [{"distance": 0.8, "entity": {"document_id": "d", "document_name": "申报书.pdf", "page_start": 11, "content": "科研项目：生成式AI赋能金融极端风险预测研究；广东省哲学社会科学规划专项小组；2024-02-27。"}}]
        if extra:
            hits.append({"distance": 0.7, "entity": {"document_id": "e", "document_name": "设备表.pdf", "page_start": 3, "content": self.sources[1]}})
        embedder, store = Mock(), Mock()
        embedder.embed.return_value = [[0.1]]
        store.search.return_value = hits
        return RagService(embedder, store, chat)

    def test_asset_claim_cannot_pass_project_list_citation(self) -> None:
        class RejectingAuditor(FakeChat):
            audits = 0

            def complete(self, system_prompt: str, user_prompt: str) -> str:
                if system_prompt == VERIFICATION_PROMPT:
                    self.audits += 1
                    payload = json.loads(user_prompt)
                    # The auditor receives ONLY the wrong cited passage, never
                    # another candidate that could accidentally justify the fact.
                    assert len(payload["sources"]) == 1
                    assert "867.23" not in payload["sources"][0]["content"]
                    return json.dumps({"claims": [{"text": payload["answer"], "verdict": "unsupported", "evidence": []}]})
                return super().complete(system_prompt, user_prompt)

        for answer in (
            "固定资产867.23万元，拥有小型机和存储系统【1】。",
            "科研项目经费867.23元【1】。",
            "科研项目于2025-02-27获批【1】。",
            "智能制造项目由该专项小组负责【1】。",
        ):
            with self.subTest(answer=answer):
                chat = RejectingAuditor(answer)
                result = self.service(chat, extra=True).answer("基础设施与设备情况？", "default")
                self.assertFalse(result.grounded)
                self.assertEqual(result.answer, REFUSAL_ZH)
                self.assertEqual(result.sources, ())
                self.assertEqual(chat.audits, 2)  # one repair, not an unbounded loop
                self.assertEqual(len(chat.prompts), 2)

    def test_repair_can_return_supported_answer(self) -> None:
        class RepairingChat(FakeChat):
            audits = 0

            def complete(self, system_prompt: str, user_prompt: str) -> str:
                if system_prompt == VERIFICATION_PROMPT:
                    self.audits += 1
                    if self.audits == 1:
                        return '{"claims": []}'
                elif self.audits:
                    self.answer = "科研项目：生成式AI赋能金融极端风险预测研究【1】。"
                return super().complete(system_prompt, user_prompt)

        chat = RepairingChat("固定资产867.23万元【1】。")
        result = self.service(chat).answer("科研项目？", "default")
        self.assertTrue(result.grounded)
        self.assertNotIn("867.23", result.answer)
        self.assertEqual(chat.audits, 2)

    def test_verifier_error_does_not_leak_unchecked_answer(self) -> None:
        class BrokenAuditor(FakeChat):
            def complete(self, system_prompt: str, user_prompt: str) -> str:
                if system_prompt == VERIFICATION_PROMPT:
                    raise RuntimeError("audit unavailable")
                return super().complete(system_prompt, user_prompt)

        self.assertFalse(self.service(BrokenAuditor()).answer("资产？", "default").grounded)

    def test_only_verified_citations_are_returned(self) -> None:
        result = self.service(FakeChat("科研项目见材料【1】。"), extra=True).answer("科研项目？", "default")
        self.assertTrue(result.grounded)
        self.assertEqual([source.index for source in result.sources], [1])


if __name__ == "__main__":
    unittest.main()

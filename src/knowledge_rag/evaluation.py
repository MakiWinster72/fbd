"""Deterministic RAG evaluation. Private ground truth and reports stay outside Git."""
from __future__ import annotations

import getpass
import json
import math
import os
import re
import sys
import tempfile
import time
import unicodedata
from collections.abc import Callable
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError, model_validator


class SourceExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    document_id: str | None = Field(default=None, min_length=1)
    document_name: str | None = Field(default=None, min_length=1)
    page_start: StrictInt | None = Field(default=None, ge=1)
    page_end: StrictInt | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_locator(self) -> SourceExpectation:
        if not self.document_id and not self.document_name:
            raise ValueError("expected source needs a document_id or document_name")
        if self.page_end is not None and (self.page_start is None or self.page_end < self.page_start):
            raise ValueError("invalid expected page range")
        return self

    def matches(self, source: EvaluationSource) -> bool:
        if self.document_id is not None and source.document_id != self.document_id:
            return False
        if self.document_name is not None and source.document_name != self.document_name:
            return False
        return self.page_start is None or (
            source.page_start <= (self.page_end or self.page_start) and source.page_end >= self.page_start
        )


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2000)
    knowledge_base_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")
    locale: Literal["zh-CN", "en"] = "zh-CN"
    expect_grounded: StrictBool
    expected_sources: list[SourceExpectation] = Field(default_factory=list)
    answer_contains: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ground_truth(self) -> EvaluationCase:
        if self.expect_grounded != bool(self.expected_sources):
            raise ValueError("answerable cases need expected_sources; refusal cases must not have them")
        if any(not term.strip() for term in self.answer_contains):
            raise ValueError("answer keywords must not be blank")
        if not self.expect_grounded and self.answer_contains:
            raise ValueError("refusal cases must not require answer keywords")
        keys = [json.dumps(source.model_dump(), sort_keys=True) for source in self.expected_sources]
        if len(set(keys)) != len(keys):
            raise ValueError("expected_sources must not contain duplicates")
        return self


class EvaluationSource(BaseModel):
    index: StrictInt = Field(ge=1)
    document_id: StrictStr
    document_name: StrictStr
    page_start: StrictInt = Field(ge=1)
    page_end: StrictInt = Field(ge=1)
    excerpt: StrictStr = ""

    @model_validator(mode="after")
    def validate_pages(self) -> EvaluationSource:
        if self.page_end < self.page_start:
            raise ValueError("invalid source page range")
        return self


class EvaluationAnswer(BaseModel):
    answer: StrictStr
    grounded: StrictBool
    sources: list[EvaluationSource]

    @model_validator(mode="after")
    def unique_indices(self) -> EvaluationAnswer:
        indices = [source.index for source in self.sources]
        if len(set(indices)) != len(indices):
            raise ValueError("source indices must be unique")
        return self


def _json_lines(path: Path) -> list[tuple[int, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append((number, json.loads(line)))
        except (ValueError, RecursionError) as exc:
            raise ValueError(f"line {number}: invalid JSON") from exc
    if not rows:
        raise ValueError("JSONL file must not be empty")
    return rows


def load_cases(path: Path) -> list[EvaluationCase]:
    cases = []
    for number, row in _json_lines(path):
        try:
            cases.append(EvaluationCase.model_validate(row))
        except ValidationError as exc:
            # Pydantic errors can contain private input values. Do not echo them.
            raise ValueError(f"line {number}: invalid evaluation case") from exc
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("case IDs must be unique")
    return cases


def load_responses(path: Path, cases: list[EvaluationCase]) -> dict[str, dict[str, Any]]:
    responses = {}
    for number, row in _json_lines(path):
        if not isinstance(row, dict) or set(row) != {"id", "response"} or not isinstance(row["id"], str):
            raise ValueError(f"line {number}: expected id and response")
        if row["id"] in responses:
            raise ValueError(f"line {number}: duplicate response ID")
        try:
            response = EvaluationAnswer.model_validate(row["response"])
        except ValidationError as exc:
            raise ValueError(f"line {number}: invalid answer response") from exc
        responses[row["id"]] = response.model_dump()
    if set(responses) != {case.id for case in cases}:
        raise ValueError("response IDs must exactly match dataset IDs")
    return responses


def _normalized(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())


def score_answer(case: EvaluationCase, answer: EvaluationAnswer, top_k: int) -> dict[str, Any]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    candidates = answer.sources[:top_k]
    relevant = [any(target.matches(source) for target in case.expected_sources) for source in candidates]
    recall = (
        sum(any(target.matches(source) for source in candidates) for target in case.expected_sources)
        / len(case.expected_sources)
        if case.expected_sources else None
    )
    reciprocal_rank = next((1 / rank for rank, match in enumerate(relevant, 1) if match), 0.0) if case.expect_grounded else None
    citations = re.findall(r"【([0-9]+)】", answer.answer)
    sources = {str(source.index): source for source in answer.sources}
    citation_validity = sum(index in sources for index in citations) / len(citations) if citations else None
    citation_precision = (
        sum(index in sources and any(target.matches(sources[index]) for target in case.expected_sources) for index in citations)
        / len(citations) if citations and case.expect_grounded else None
    )
    keyword_match = all(_normalized(term) in _normalized(answer.answer) for term in case.answer_contains)
    grounded_correct = answer.grounded == case.expect_grounded
    refusal_correct = (not answer.grounded and not answer.sources and not citations) if not case.expect_grounded else None
    passed = (
        grounded_correct and keyword_match and recall == 1 and citation_validity == 1 and citation_precision == 1
        if case.expect_grounded else bool(refusal_correct)
    )
    return {
        "passed": passed, "grounded_correct": grounded_correct,
        "recall_at_k": recall, "reciprocal_rank": reciprocal_rank,
        "citation_validity": citation_validity, "citation_evidence_precision": citation_precision,
        "citation_count": len(citations), "refusal_correct": refusal_correct,
        "keyword_match": keyword_match if case.answer_contains else None,
        "returned_sources": len(answer.sources), "evaluated_sources": len(candidates),
    }


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row[key] for row in rows if row.get(key) is not None]
    return sum(values) / len(values) if values else None


def evaluate_cases(
    cases: list[EvaluationCase], ask: Callable[[EvaluationCase], dict[str, Any]], top_k: int = 6,
    mode: Literal["live", "offline"] = "live",
) -> dict[str, Any]:
    if not cases or top_k <= 0:
        raise ValueError("cases must not be empty and top_k must be positive")
    results = []
    for case in cases:
        started = time.perf_counter()
        try:
            answer = EvaluationAnswer.model_validate(ask(case))
            result = {"id": case.id, "question": case.question, "knowledge_base_id": case.knowledge_base_id,
                      "status": "ok", **score_answer(case, answer, top_k), "response": answer.model_dump()}
        except Exception as exc:
            # Never copy provider/transport errors which may contain credentials or request bodies.
            error = f"HTTP {exc.code}" if isinstance(exc, HTTPError) else type(exc).__name__
            result = {"id": case.id, "status": "error", "passed": False, "error": error}
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 3) if mode == "live" else None
        results.append(result)
    successful = [row for row in results if row["status"] == "ok"]
    latencies = sorted(row["latency_ms"] for row in results if row["latency_ms"] is not None)
    return {
        "schema_version": 1, "mode": mode, "top_k": top_k,
        "summary": {
            "cases": len(cases), "successful": len(successful), "errors": len(cases) - len(successful),
            "passed": sum(row["passed"] for row in results), "pass_rate": sum(row["passed"] for row in results) / len(cases),
            "recall_at_k": _mean(successful, "recall_at_k"), "mrr": _mean(successful, "reciprocal_rank"),
            "citation_validity": _mean(successful, "citation_validity"),
            "citation_evidence_precision": _mean(successful, "citation_evidence_precision"),
            "refusal_accuracy": _mean(successful, "refusal_correct"), "grounded_accuracy": _mean(successful, "grounded_correct"),
            "keyword_accuracy": _mean(successful, "keyword_match"),
            "latency_mean_ms": sum(latencies) / len(latencies) if latencies else None,
            "latency_p95_ms": latencies[math.ceil(0.95 * len(latencies)) - 1] if latencies else None,
        }, "results": results,
    }


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class EvaluationApiClient:
    def __init__(self, base_url: str, timeout: float = 120):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base URL must be HTTP(S), without credentials, query or fragment")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("non-local evaluation requires HTTPS")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()), _NoRedirect())

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        request = Request(self.base_url + path, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
        with self.opener.open(request, timeout=self.timeout) as response:
            try:
                result = json.load(response)
            except (ValueError, RecursionError) as exc:
                raise ValueError("invalid API JSON response") from exc
        if not isinstance(result, dict):
            raise ValueError("API response must be an object")
        return result

    def login(self, username: str, password: str) -> None:
        response = self._post("/api/v1/auth/login", {"username": username, "password": password})
        if not response.get("id") or response.get("username") != username:
            raise ValueError("invalid login response")

    def ask(self, case: EvaluationCase) -> dict[str, Any]:
        return self._post("/api/v1/chat", {"question": case.question, "knowledge_base_id": case.knowledge_base_id, "locale": case.locale})

    def logout(self) -> None:
        request = Request(self.base_url + "/api/v1/auth/logout", data=b"", method="POST")
        with self.opener.open(request, timeout=self.timeout):
            pass


def run_command(args: Any) -> int:
    client = None
    logged_in = False
    try:
        if args.top_k <= 0 or not math.isfinite(args.min_pass_rate) or not 0 <= args.min_pass_rate <= 1:
            raise ValueError("top-k must be positive; min-pass-rate must be between 0 and 1")
        protected = [args.dataset] + ([args.responses] if args.responses is not None else [])
        if args.output.resolve() in {path.resolve() for path in protected}:
            raise ValueError("report must not overwrite dataset or responses")
        cases = load_cases(args.dataset)
        if args.responses is not None:
            if args.username or args.password_env:
                raise ValueError("offline scoring does not accept login options")
            responses = load_responses(args.responses, cases)
            report = evaluate_cases(cases, lambda case: responses[case.id], args.top_k, "offline")
        else:
            if not args.username:
                raise ValueError("live evaluation requires --username")
            client = EvaluationApiClient(args.base_url, args.timeout)
            password = os.environ.get(args.password_env) if args.password_env else getpass.getpass("评测账号密码：")
            if not password:
                raise ValueError("evaluation password must not be empty")
            client.login(args.username, password)
            logged_in = True
            report = evaluate_cases(cases, client.ask, args.top_k)
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".evaluation-", suffix=".json", dir=args.output.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(report, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, args.output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        return 0 if not report["summary"]["errors"] and report["summary"]["pass_rate"] >= args.min_pass_rate else 2
    except (OSError, ValueError, EOFError) as exc:
        # Validation text is controlled here; OS/network details are intentionally withheld.
        message = str(exc) if type(exc) is ValueError else type(exc).__name__
        print(f"评测失败：{message}", file=sys.stderr)
        return 1
    finally:
        if client is not None and logged_in:
            try:
                client.logout()
            except Exception:
                print("评测会话退出失败，请检查服务或等待会话过期。", file=sys.stderr)

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError

import pytest

from knowledge_rag.cli import main
from knowledge_rag.evaluation import (
    EvaluationAnswer,
    EvaluationApiClient,
    EvaluationCase,
    evaluate_cases,
    load_cases,
    load_responses,
    score_answer,
)


def source(index=1, document_id="doc-a", page=2):
    return {"index": index, "document_id": document_id, "document_name": "材料.pdf",
            "page_start": page, "page_end": page, "excerpt": "预算为120万元。"}


def answer(text="预算为120万元【1】", sources=None, grounded=True):
    return {"answer": text, "grounded": grounded, "sources": [source()] if sources is None else sources}


def case(identifier="budget", **kwargs):
    return EvaluationCase.model_validate({"id": identifier, "question": "预算是多少？", "knowledge_base_id": "default",
        "expect_grounded": True, "expected_sources": [{"document_id": "doc-a", "page_start": 2}],
        "answer_contains": ["120万元"], **kwargs})


def write_jsonl(path: Path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_recall_and_mrr_use_order_pages_and_top_k():
    rows = [source(1, "irrelevant"), source(2), source(3, "doc-b", 4)]
    question = case(expected_sources=[{"document_id": "doc-a", "page_start": 2}, {"document_id": "doc-b", "page_start": 4}])
    response = EvaluationAnswer.model_validate(answer("120万元【2】【3】", rows))
    partial = score_answer(question, response, 2)
    assert partial["recall_at_k"] == 0.5
    assert partial["reciprocal_rank"] == 0.5
    assert not partial["passed"]
    assert score_answer(question, response, 3)["passed"]
    wrong_page = EvaluationAnswer.model_validate(answer(sources=[source(page=3)]))
    assert score_answer(case(), wrong_page, 6)["recall_at_k"] == 0
    assert score_answer(case(), EvaluationAnswer.model_validate(answer()), 6)["passed"]


@pytest.mark.parametrize("text,validity,precision", [
    ("120万元【1】【9】", 0.5, 0.5),
    ("120万元", None, None),
    ("120万元【2】", 1.0, 0.0),
])
def test_citation_presence_invalid_ids_and_irrelevant_evidence(text, validity, precision):
    result = score_answer(case(), EvaluationAnswer.model_validate(answer(text, [source(), source(2, "other")])), 6)
    assert result["citation_validity"] == validity
    assert result["citation_evidence_precision"] == precision
    assert not result["passed"]


def test_refusal_requires_no_sources_or_citations_and_keywords_are_normalized():
    refusal = case("refusal", expect_grounded=False, expected_sources=[], answer_contains=[])
    for response, expected in [(answer("没有依据", [], False), True), (answer("没有依据", [], True), False),
                               (answer("没有依据", [source()], False), False), (answer("没有依据【8】", [], False), False)]:
        assert score_answer(refusal, EvaluationAnswer.model_validate(response), 6)["passed"] is expected
    result = score_answer(case(answer_contains=["ＡB c", "120万元"]),
        EvaluationAnswer.model_validate(answer("ab C：120 万元【1】")), 6)
    assert result["keyword_match"] and result["passed"]


def test_errors_are_counted_without_leaking_and_offline_has_no_latency():
    def ask(question):
        if question.id == "failed":
            raise RuntimeError("SECRET-PASSWORD provider detail")
        return answer()
    report = evaluate_cases([case("failed"), case("ok")], ask)
    assert report["summary"]["errors"] == 1
    assert report["summary"]["pass_rate"] == 0.5
    assert report["summary"]["recall_at_k"] == 1
    assert report["summary"]["latency_p95_ms"] >= 0
    assert "SECRET" not in json.dumps(report)
    offline = evaluate_cases([case()], lambda _: answer(), mode="offline")
    assert offline["summary"]["latency_mean_ms"] is None
    assert offline["summary"]["refusal_accuracy"] is None
    assert offline["results"][0]["latency_ms"] is None


@pytest.mark.parametrize("changes", [
    {"expect_grounded": "false"}, {"expected_sources": []},
    {"expected_sources": [{"document_id": "doc-a", "page_start": 4, "page_end": 2}]},
    {"expected_sources": [{"page_start": 1}]}, {"answer_contains": ["   "]},
    {"locale": "fr"}, {"unexpected": "secret"},
])
def test_invalid_cases_are_rejected_without_echoing_private_input(tmp_path, changes):
    dataset = tmp_path / "questions.jsonl"
    row = case().model_dump()
    row.update(changes)
    write_jsonl(dataset, [row])
    with pytest.raises(ValueError, match="invalid evaluation case") as error:
        load_cases(dataset)
    assert "secret" not in str(error.value)


def test_duplicate_and_mismatched_ids_are_rejected(tmp_path):
    dataset = tmp_path / "questions.jsonl"
    write_jsonl(dataset, [case().model_dump()] * 2)
    with pytest.raises(ValueError, match="unique"):
        load_cases(dataset)
    responses = tmp_path / "responses.jsonl"
    write_jsonl(responses, [{"id": "other", "response": answer()}])
    with pytest.raises(ValueError, match="exactly match"):
        load_responses(responses, [case()])
    write_jsonl(responses, [{"id": "budget", "response": answer()}] * 2)
    with pytest.raises(ValueError, match="duplicate"):
        load_responses(responses, [case()])


def test_cli_offline_exit_codes_private_atomic_report_and_input_protection(tmp_path, capsys):
    dataset, responses, report = [tmp_path / name for name in ("questions.jsonl", "responses.jsonl", "report.json")]
    write_jsonl(dataset, [case().model_dump()])
    write_jsonl(responses, [{"id": "budget", "response": answer()}])
    args = ["evaluate", str(dataset), "--responses", str(responses), "--output", str(report)]
    assert main(args) == 0
    assert report.stat().st_mode & 0o777 == 0o600
    assert json.loads(report.read_text())["mode"] == "offline"
    assert not list(tmp_path.glob(".evaluation-*"))
    write_jsonl(responses, [{"id": "budget", "response": answer("wrong answer")}])
    assert main(args) == 2
    assert main(args + ["--min-pass-rate", "0"]) == 0
    assert main(args + ["--top-k", "0"]) == 1
    assert main(args + ["--output", str(dataset)]) == 1
    assert load_cases(dataset)[0].id == "budget"
    assert "report must not overwrite" in capsys.readouterr().err


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com", "http://example.com", "https://u:p@example.com", "https://example.com?token=secret"])
def test_client_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        EvaluationApiClient(url)


def test_client_login_cookie_payload_logout_and_redirect_protection():
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}")
            seen.append((self.path, payload, self.headers.get("Cookie")))
            if self.path.endswith("/auth/login") and payload["username"] == "redirect":
                self.send_response(302)
                self.send_header("Location", "/trap")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if self.path.endswith("/auth/login"):
                self.send_header("Set-Cookie", "session=test; Path=/; HttpOnly")
                value = {"id": "user-1", "username": payload["username"]}
            elif self.path.endswith("/chat"):
                value = answer()
            else:
                value = {}
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    worker.start()
    try:
        client = EvaluationApiClient(f"http://127.0.0.1:{server.server_port}")
        client.login("evaluation-user", "synthetic-password")
        assert client.ask(case(locale="en"))["grounded"]
        client.logout()
        assert seen[1][1] == {"question": "预算是多少？", "knowledge_base_id": "default", "locale": "en"}
        assert seen[1][2] == "session=test"
        assert seen[2][0].endswith("/auth/logout")
        with pytest.raises(HTTPError) as error:
            client.login("redirect", "synthetic-password")
        assert error.value.code == 302
        assert len(seen) == 4 and all(row[0] != "/trap" for row in seen)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)

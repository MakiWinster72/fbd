#!/usr/bin/env python3
"""Isolated live smoke test: MinerU, configured models, Milvus, upload and retry.

Run from the repository root: uv run --extra milvus --extra test python scripts/e2e_smoke.py
Only synthetic files are sent to MinerU and the configured embedding/chat providers.
The existing business DB and Milvus collection are never written to.
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from fastapi.testclient import TestClient
from pymilvus import MilvusClient

from knowledge_rag.admin_store import AdminStore
from knowledge_rag.api import create_app
from knowledge_rag.config import Settings
from knowledge_rag.db import build_engine
from knowledge_rag.security import SecretCipher


KB = "default"


def synthetic_docx(text: str) -> bytes:
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    xml = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{escaped}</w:t></w:r></w:p></w:body></w:document>'
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def synthetic_pdf(text: str) -> bytes:
    assert text.isascii() and "(" not in text and ")" not in text
    content = f"BT /F1 14 Tf 48 740 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Root 1 0 R /Size {len(offsets)} >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(result)


def check(response, expected: int, label: str) -> dict:
    if response.status_code != expected:
        raise RuntimeError(f"{label}: HTTP {response.status_code}: {response.text[:400]}")
    return response.json()


def wait_job(client: TestClient, job_id: str, label: str, timeout: int = 900) -> dict:
    deadline = time.monotonic() + timeout
    status = "queued"
    while time.monotonic() < deadline:
        job = check(client.get(f"/api/v1/admin/document-imports/{job_id}"), 200, label)
        if job["status"] != status:
            status = job["status"]
            print(f"{label}: {status} ({job['completed_chunks']}/{job['total_chunks']})", flush=True)
        if status in {"completed", "failed", "discarded"}:
            return job
        time.sleep(2)
    raise TimeoutError(f"{label}: timeout after {timeout} seconds in status {status}")


def submit(client: TestClient, name: str, body: bytes, ocr_mode: str = "auto") -> dict:
    return check(client.post(
        "/api/v1/admin/document-imports",
        data={"knowledge_base_id": KB, "ocr_mode": ocr_mode},
        files={"file": (name, body)},
    ), 202, f"upload {name}")


def main() -> None:
    live_settings = Settings.from_env()
    # Only READ the existing configuration. AdminStore.__init__ performs writes
    # (session cleanup and job recovery), so deliberately avoid calling it here.
    live_store = AdminStore.__new__(AdminStore)
    live_database = live_settings.database_url or f"sqlite:///{live_settings.app_db_path.resolve()}"
    live_store.engine = build_engine(live_database)
    live_store.cipher = SecretCipher(live_settings.model_secret_key)
    try:
        active = {}
        for purpose in ("embedding", "chat", "reranker"):
            profile_id = live_store.get_setting(f"active_{purpose}_profile_id")
            active[purpose] = live_store.get_profile(profile_id) if profile_id else None
    finally:
        live_store.engine.dispose()
    if not active["embedding"] or not active["chat"]:
        raise RuntimeError("需要先配置活动的 Embedding 和 Chat 模型")

    collection = f"rag_e2e_{uuid4().hex[:16]}"
    workspace = Path(tempfile.mkdtemp(prefix="rag-e2e-"))
    settings = replace(live_settings, database_url="", app_db_path=workspace / "app.db",
                       data_dir=workspace / "data", milvus_collection=collection,
                       milvus_vector_dim=active["embedding"].vector_dim,
                       bootstrap_admin_username="", bootstrap_admin_password="",
                       auth_cookie_secure=False)
    client = None
    worker_active = False
    try:
        store = AdminStore(settings.app_db_path, settings.model_secret_key)
        store.seed_defaults(settings)
        admin_password = uuid4().hex
        admin = store.create_user("e2e-admin", "隔离验收", admin_password, "admin")
        test_profiles = {}
        for purpose, profile in active.items():
            if profile is None:
                continue
            test_profiles[purpose] = store.create_profile(
                name=f"E2E {purpose}", purpose=purpose, protocol=profile.protocol,
                base_url=profile.base_url, api_key=profile.api_key, model_name=profile.model_name,
                vector_dim=profile.vector_dim, options=profile.options, actor_id=admin.id,
            )
            store.set_setting(f"active_{purpose}_profile_id", test_profiles[purpose].id)
        store.set_setting("active_collection_name", collection)
        store.set_setting("active_vector_dim", str(active["embedding"].vector_dim))
        app = create_app(settings=settings, admin_store=store)
        client = TestClient(app)
        # Use a new random credential, never the real administrator's password.
        check(client.post("/api/v1/auth/login", json={"username": "e2e-admin", "password": admin_password}), 200, "login")
        pdf_body = synthetic_pdf("Synthetic milestone for E2E acceptance occurs on 2031-07-19. This sentence is only test data and is safe for the remote embedding provider.")
        pdf_path = workspace / "synthetic.pdf"
        pdf_path.write_bytes(pdf_body)
        subprocess.run(["pdfinfo", str(pdf_path)], check=True, capture_output=True)
        print("isolated collection prepared; synthetic PDF validated", flush=True)

        first = submit(client, "e2e-synthetic.docx", synthetic_docx("The synthetic acceptance milestone is 2031-07-19. This document exists only inside the isolated end-to-end test."))
        worker_active = True
        result = wait_job(client, first["id"], "DOCX")
        worker_active = False
        if result["status"] != "completed":
            raise RuntimeError(f"DOCX import failed: {result['error']}")

        second = submit(client, "e2e-synthetic.pdf", pdf_body)
        worker_active = True
        result = wait_job(client, second["id"], "PDF through MinerU")
        worker_active = False
        if result["status"] != "completed":
            raise RuntimeError(f"PDF import failed: {result['error']}")

        documents = check(client.get("/api/v1/admin/documents", params={"knowledge_base_id": KB}), 200, "documents")
        assert len(documents) == 2, f"expected 2 isolated documents, got {len(documents)}"
        search = check(client.post("/api/v1/search", json={"knowledge_base_id": KB, "question": "What is the date of the synthetic acceptance milestone?"}), 200, "search")
        assert search["sources"], "synthetic documents not retrieved"
        catalog = check(client.post("/api/v1/chat", json={"knowledge_base_id": KB, "question": "知识库里有什么文件？"}), 200, "catalog chat")
        assert "2 份文档" in catalog["answer"]
        answer = check(client.post("/api/v1/chat", json={"knowledge_base_id": KB, "question": "When does the synthetic acceptance milestone occur?"}), 200, "grounded chat")
        assert answer["grounded"] and answer["sources"], "chat did not return grounded sources"
        print("isolated retrieval and grounded chat: passed", flush=True)

        embedding = test_profiles["embedding"]
        store.update_profile(embedding.id, {"base_url": "http://127.0.0.1:9"}, admin.id)
        failed = submit(client, "e2e-retry.docx", synthetic_docx("This synthetic retry document contains a made-up verification fact for 2032-04-18."))
        worker_active = True
        result = wait_job(client, failed["id"], "intentional provider failure")
        worker_active = False
        assert result["status"] == "failed" and result["source_available"], "failed source not retained"
        store.update_profile(embedding.id, {"base_url": embedding.base_url}, admin.id)
        retry = check(client.post(f"/api/v1/admin/document-imports/{failed['id']}/retry"), 202, "retry")
        assert retry["id"] == failed["id"]
        worker_active = True
        result = wait_job(client, retry["id"], "retry")
        worker_active = False
        if result["status"] != "completed":
            raise RuntimeError(f"retry failed: {result['error']}")
        print("failed source retention and retry: passed", flush=True)

        store.update_profile(embedding.id, {"base_url": "http://127.0.0.1:9"}, admin.id)
        discard = submit(client, "e2e-discard.docx", synthetic_docx("Discardable synthetic upload, never user content."))
        worker_active = True
        result = wait_job(client, discard["id"], "intentional discard failure")
        worker_active = False
        assert result["status"] == "failed" and result["source_available"]
        discarded = check(client.post(f"/api/v1/admin/document-imports/{discard['id']}/discard"), 200, "discard")
        assert discarded["status"] == "discarded" and not discarded["source_available"]
        assert client.post(f"/api/v1/admin/document-imports/{discard['id']}/retry").status_code == 409
        print("failed upload cleanup: passed", flush=True)
        print("E2E ACCEPTANCE PASSED", flush=True)
    finally:
        if client is not None:
            client.close()
        if worker_active:
            print("WARNING: worker still active; isolated artifacts retained for safe recovery", flush=True)
            print(f"isolated collection: {collection}; workspace: {workspace}", flush=True)
        else:
            try:
                milvus = MilvusClient(uri=live_settings.milvus_uri)
                if collection.startswith("rag_e2e_") and milvus.has_collection(collection_name=collection):
                    milvus.drop_collection(collection_name=collection)
                print("isolated Milvus collection cleaned", flush=True)
            finally:
                shutil.rmtree(workspace)


if __name__ == "__main__":
    main()

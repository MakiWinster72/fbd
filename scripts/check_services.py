#!/usr/bin/env python3
"""Check local RAG infrastructure without using HTTP proxy settings."""

from __future__ import annotations

import json
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    name: str
    host: str
    port: int
    path: str | None = None
    expect_json: bool = False


CHECKS = (
    Check("ollama", "127.0.0.1", 11434, "/api/tags", expect_json=True),
    Check("mineru", "127.0.0.1", 7860, "/config", expect_json=True),
    Check("milvus-grpc", "127.0.0.1", 19530),
    Check("milvus-health", "127.0.0.1", 9091, "/healthz"),
)


def check_tcp(check: Check, timeout: float = 2.0) -> tuple[bool, str]:
    started = time.monotonic()
    try:
        with socket.create_connection((check.host, check.port), timeout=timeout):
            elapsed = (time.monotonic() - started) * 1000
            return True, f"TCP connected in {elapsed:.0f} ms"
    except OSError as exc:
        elapsed = (time.monotonic() - started) * 1000
        return False, f"TCP failed in {elapsed:.0f} ms: {exc}"


def check_http(check: Check, timeout: float = 5.0) -> tuple[bool, str]:
    url = f"http://{check.host}:{check.port}{check.path}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.monotonic()
    try:
        with opener.open(url, timeout=timeout) as response:
            body = response.read()
            elapsed = (time.monotonic() - started) * 1000
            if response.status != 200:
                return False, f"HTTP {response.status} in {elapsed:.0f} ms"
            if check.expect_json:
                json.loads(body)
            return True, f"HTTP {response.status} in {elapsed:.0f} ms"
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        elapsed = (time.monotonic() - started) * 1000
        return False, f"HTTP failed in {elapsed:.0f} ms: {exc}"


def main() -> int:
    failed = 0
    for check in CHECKS:
        ok, detail = check_tcp(check)
        if ok and check.path:
            ok, detail = check_http(check)
        state = "PASS" if ok else "FAIL"
        print(f"[{state}] {check.name}: {detail}")
        failed += not ok
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

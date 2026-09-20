#!/usr/bin/env python3
"""Submit one document to the local MinerU Gradio API and save its outputs."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import sys
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


BASE_URL = "http://127.0.0.1:7860"
API_NAME = "convert_to_markdown_stream"


def opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def encode_multipart(path: Path) -> tuple[bytes, str]:
    boundary = f"----mineru-{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="files"; filename="{path.name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def upload(client: urllib.request.OpenerDirector, path: Path) -> str:
    body, content_type = encode_multipart(path)
    request = urllib.request.Request(
        f"{BASE_URL}/gradio_api/upload",
        data=body,
        headers={"Content-Type": content_type},
    )
    with client.open(request, timeout=120) as response:
        result = json.load(response)
    if not isinstance(result, list) or not result:
        raise RuntimeError(f"Unexpected upload response: {result!r}")
    return result[0]


def submit(
    client: urllib.request.OpenerDirector,
    uploaded_path: str,
    original: Path,
    max_pages: int,
    force_ocr: bool,
) -> str:
    file_data = {
        "path": uploaded_path,
        "orig_name": original.name,
        "mime_type": mimetypes.guess_type(original.name)[0] or "application/pdf",
        "meta": {"_type": "gradio.FileData"},
    }
    payload = {
        "data": [
            file_data,
            max_pages,
            force_ocr,
            True,
            True,
            True,
            "medium",
            "ch (Chinese, English, Japanese, Chinese Traditional, Latin)",
            "hybrid-engine",
            "http://localhost:30000",
        ]
    }
    request = urllib.request.Request(
        f"{BASE_URL}/gradio_api/call/{API_NAME}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with client.open(request, timeout=30) as response:
        result = json.load(response)
    event_id = result.get("event_id")
    if not event_id:
        raise RuntimeError(f"Missing event_id: {result!r}")
    return event_id


def wait_for_result(client: urllib.request.OpenerDirector, event_id: str) -> list[Any]:
    url = f"{BASE_URL}/gradio_api/call/{API_NAME}/{event_id}"
    request = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    event = None
    final_data: list[Any] | None = None
    with client.open(request, timeout=900) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event:"):
                event = line.partition(":")[2].strip()
            elif line.startswith("data:"):
                data_text = line.partition(":")[2].strip()
                if event == "error":
                    raise RuntimeError(f"MinerU job failed: {data_text}")
                if event in {"generating", "complete"}:
                    parsed = json.loads(data_text)
                    if isinstance(parsed, list):
                        final_data = parsed
                if event == "complete":
                    break
    if final_data is None:
        raise RuntimeError("MinerU stream ended without result data")
    return final_data


def download_file(client: urllib.request.OpenerDirector, item: Any, output_dir: Path) -> Path | None:
    if not isinstance(item, dict):
        return None
    url = item.get("url")
    path = item.get("path")
    if not url and path:
        url = f"{BASE_URL}/gradio_api/file={urllib.parse.quote(path)}"
    if not url:
        return None
    parts = urllib.parse.urlsplit(url)
    url = urllib.parse.urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            urllib.parse.quote(parts.path, safe="/=@:"),
            urllib.parse.quote(parts.query, safe="=&?/:,"),
            parts.fragment,
        )
    )
    name = item.get("orig_name") or (Path(path).name if path else "mineru-result.bin")
    destination = output_dir / Path(name).name
    with client.open(url, timeout=120) as response, destination.open("wb") as target:
        shutil.copyfileobj(response, target)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=1000)
    parser.add_argument("--force-ocr", action="store_true")
    args = parser.parse_args()

    source = args.input.resolve()
    if not source.is_file():
        print(f"Input file not found: {source}", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)

    client = opener()
    print(f"Uploading: {source.name}", flush=True)
    uploaded_path = upload(client, source)
    print(f"Uploaded to: {uploaded_path}", flush=True)
    event_id = submit(client, uploaded_path, source, args.max_pages, args.force_ocr)
    print(f"Event: {event_id}", flush=True)
    result = wait_for_result(client, event_id)

    result_path = args.output_dir / "gradio-result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Result metadata: {result_path}")

    downloaded = download_file(client, result[1] if len(result) > 1 else None, args.output_dir)
    if downloaded:
        print(f"Downloaded: {downloaded}")
    markdown = result[3] if len(result) > 3 and isinstance(result[3], str) else ""
    content_json = result[4] if len(result) > 4 and isinstance(result[4], str) else ""
    (args.output_dir / "document.md").write_text(markdown)
    (args.output_dir / "content_list.json").write_text(content_json)
    print(f"Markdown chars: {len(markdown)}")
    print(f"Content JSON chars: {len(content_json)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

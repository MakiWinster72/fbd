from __future__ import annotations

import argparse
import getpass
import json
import socket
import sys
from pathlib import Path

from knowledge_rag.admin_store import AdminStore
from knowledge_rag.config import Settings
from knowledge_rag.pipeline import IngestionPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="knowledge-rag")
    subcommands = parser.add_subparsers(dest="command", required=True)

    prepare = subcommands.add_parser("prepare", help="normalize and chunk one document")
    prepare.add_argument("source", type=Path)
    prepare.add_argument("--knowledge-base", default="default")
    prepare.add_argument("--mineru-content-list", type=Path)

    ingest = subcommands.add_parser("ingest", help="prepare, embed and insert one document into Milvus")
    ingest.add_argument("source", type=Path)
    ingest.add_argument("--knowledge-base", default="default")
    ingest.add_argument("--mineru-content-list", type=Path)

    subcommands.add_parser("health", help="check Ollama, MinerU and Milvus TCP ports")

    create_admin = subcommands.add_parser("create-admin", help="create an administrator account")
    create_admin.add_argument("username")
    create_admin.add_argument("--display-name", default="系统管理员")
    create_admin.add_argument("--password", help="omit to enter the password securely")
    return parser


def check_port(name: str, host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"[PASS] {name}: {host}:{port}")
            return True
    except OSError as exc:
        print(f"[FAIL] {name}: {exc}")
        return False


def run_health() -> int:
    checks = [
        check_port("ollama", "127.0.0.1", 11434),
        check_port("mineru", "127.0.0.1", 7860),
        check_port("milvus", "127.0.0.1", 19530),
    ]
    return 0 if all(checks) else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "health":
        return run_health()

    settings = Settings.from_env()
    if args.command == "create-admin":
        password = args.password or getpass.getpass("管理员密码：")
        store = AdminStore(settings.database_url or settings.app_db_path, settings.model_secret_key)
        try:
            user = store.create_user(args.username, args.display_name, password, "admin")
        except Exception as exc:
            print(f"创建管理员失败：{exc}", file=sys.stderr)
            return 1
        print(json.dumps({"id": user.id, "username": user.username, "role": user.role}, ensure_ascii=False))
        return 0

    pipeline = IngestionPipeline(settings)
    prepared = pipeline.prepare(args.source, args.knowledge_base, args.mineru_content_list)
    if args.command == "prepare":
        print(
            json.dumps(
                {
                    "document_id": prepared.document.document_id,
                    "pages": prepared.document.page_count,
                    "blocks": len(prepared.document.blocks),
                    "chunks": len(prepared.chunks),
                    "fallback_pages": [
                        item.page_number for item in prepared.document.page_quality if item.fallback_used
                    ],
                    "review_pages": [
                        item.page_number for item in prepared.document.page_quality if item.review_required
                    ],
                    "output_dir": str(prepared.output_dir),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    embedded = pipeline.embed(prepared)
    inserted = pipeline.index(embedded)
    print(json.dumps({"inserted": inserted, "collection": settings.milvus_collection}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

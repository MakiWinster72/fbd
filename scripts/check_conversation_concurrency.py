"""Run history concurrency probes against an isolated, disposable PostgreSQL container.

Requires Docker and a locally cached postgres:17-alpine image. No image pulls,
real databases, RAG services, host-wide ports, or persisted volumes are used.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    token = uuid4().hex[:12]
    name = f"knowledge-history-qa-{token}"
    database = f"knowledge_history_qa_{token}"
    password = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix="knowledge-history-postgres-") as temporary:
        environment_file = Path(temporary) / "postgres.env"
        environment_file.touch(mode=0o600)
        environment_file.write_text(f"POSTGRES_USER=qa\nPOSTGRES_DB={database}\nPOSTGRES_PASSWORD={password}\n", encoding="utf-8")
        # Check the local cache explicitly: never pull an image to run this probe.
        subprocess.run(["docker", "image", "inspect", "postgres:17-alpine"], check=True, stdout=subprocess.DEVNULL)
        launched = False
        try:
            subprocess.run(["docker", "run", "--pull=never", "--rm", "-d", "--name", name, "--label", "knowledge.qa=conversation-concurrency", "--env-file", str(environment_file), "--tmpfs", "/var/lib/postgresql/data:rw,size=512m", "-p", "127.0.0.1::5432", "postgres:17-alpine"], check=True, stdout=subprocess.DEVNULL)
            launched = True
            deadline = time.monotonic() + 45
            while True:
                ready = subprocess.run(["docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "qa", "-d", database], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if ready.returncode == 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("temporary PostgreSQL did not become ready")
                time.sleep(0.2)
            # No psql URI, credentials, or container inspection dump in the output.
            bindings = json.loads(subprocess.check_output(["docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", name], text=True))
            port = bindings["5432/tcp"][0]["HostPort"]
            environment = dict(os.environ, KNOWLEDGE_TEST_POSTGRES_URL=f"postgresql+psycopg://qa:{password}@127.0.0.1:{port}/{database}")
            result = subprocess.run(["uv", "run", "--with", "pytest", "--extra", "test", "pytest", "-q", "tests/test_conversation_concurrency.py"], cwd=project, env=environment, timeout=150)
            return result.returncode
        finally:
            if launched:
                subprocess.run(["docker", "rm", "-f", name], check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    raise SystemExit(main())

"""Durable file operations and a repairable intent for Milvus/cache commits.

Milvus and the business DB do not share a transaction. An intent records the
collection and document being changed before the first external index write;
on restart a failed new-document write can be compensated under its lease.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_json(path: Path, payload: object) -> None:
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2).encode())


@dataclass(frozen=True)
class IndexIntent:
    job_id: str
    knowledge_base_id: str
    document_id: str
    collection: str
    already_indexed: bool

    def path(self, data_dir: Path) -> Path:
        return data_dir / "index-intents" / f"{self.job_id}.json"

    def persist(self, data_dir: Path) -> None:
        atomic_json(self.path(data_dir), asdict(self))

    def clear(self, data_dir: Path) -> None:
        path = self.path(data_dir)
        path.unlink(missing_ok=True)
        if path.parent.exists():
            _sync_directory(path.parent)


def pending_intents(data_dir: Path) -> list[IndexIntent]:
    directory = data_dir / "index-intents"
    if not directory.exists():
        return []
    intents: list[IndexIntent] = []
    for path in sorted(directory.glob("*.json")):
        try:
            intents.append(IndexIntent(**json.loads(path.read_text())))
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError(f"Invalid index intent file {path.name}") from exc
    return intents

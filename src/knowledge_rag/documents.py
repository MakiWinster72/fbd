from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path


VERSION_SUFFIXES = re.compile(
    r"(?:[-_（(\s]*(?:pdf版本|扫描件|最终版|最新版|修订版|副本)[-）)\s]*)+$",
    re.IGNORECASE,
)


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_id(*parts: str) -> str:
    value = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def normalized_document_name(path: Path) -> str:
    name = unicodedata.normalize("NFKC", path.stem).strip()
    name = VERSION_SUFFIXES.sub("", name)
    name = re.sub(r"\s+", "", name)
    return name.casefold()


def document_identity(path: Path) -> tuple[str, str, str]:
    file_hash = sha256_file(path)
    document_id = stable_id("document", file_hash)
    logical_document_id = stable_id("logical-document", normalized_document_name(path))
    return document_id, logical_document_id, file_hash

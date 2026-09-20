from __future__ import annotations

from dataclasses import asdict
import os
import re
from typing import Any
from urllib.parse import urlsplit

from knowledge_rag.models import Chunk


class MilvusChunkStore:
    def __init__(self, uri: str, collection: str, vector_dim: int) -> None:
        host = urlsplit(uri).hostname
        if host:
            for key in ("NO_PROXY", "no_proxy"):
                values = [item.strip() for item in os.environ.get(key, "").split(",") if item.strip()]
                if host not in values:
                    values.append(host)
                os.environ[key] = ",".join(values)
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:
            raise RuntimeError("Milvus support requires: uv sync --extra milvus") from exc
        self.client = MilvusClient(uri=uri)
        self.collection = collection
        self.vector_dim = vector_dim

    def ensure_collection(self) -> None:
        if self.client.has_collection(collection_name=self.collection):
            return
        self.client.create_collection(
            collection_name=self.collection,
            dimension=self.vector_dim,
            primary_field_name="id",
            id_type="string",
            vector_field_name="vector",
            metric_type="COSINE",
            auto_id=False,
            max_length=64,
            enable_dynamic_field=True,
        )

    def upsert(self, chunks: list[Chunk]) -> int:
        self.ensure_collection()
        rows: list[dict[str, Any]] = []
        for chunk in chunks:
            if chunk.vector is None:
                raise ValueError(f"Chunk has no vector: {chunk.id}")
            row = asdict(chunk)
            row["vector"] = list(chunk.vector)
            row["block_types"] = list(chunk.block_types)
            rows.append(row)
        if not rows:
            return 0
        self.client.upsert(collection_name=self.collection, data=rows)
        self.client.flush(collection_name=self.collection)
        return len(rows)

    def search(self, vector: list[float], limit: int = 10, knowledge_base_id: str | None = None) -> list[dict[str, Any]]:
        if knowledge_base_id and not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", knowledge_base_id):
            raise ValueError("Invalid knowledge_base_id")
        expression = f'knowledge_base_id == "{knowledge_base_id}"' if knowledge_base_id else ""
        result = self.client.search(
            collection_name=self.collection,
            data=[vector],
            limit=limit,
            filter=expression,
            output_fields=[
                "document_id",
                "logical_document_id",
                "knowledge_base_id",
                "document_name",
                "page_start",
                "page_end",
                "section_title",
                "content",
                "content_hash",
                "block_types",
            ],
        )
        return result[0] if result else []

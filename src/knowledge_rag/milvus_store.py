from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
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

    def has_collection(self) -> bool:
        return self.client.has_collection(collection_name=self.collection)

    def ensure_collection(self) -> None:
        if self.has_collection():
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

    def document_chunks(self, knowledge_base_id: str, document_id: str | None = None, include_content: bool = False) -> list[dict[str, Any]]:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", knowledge_base_id):
            raise ValueError("Invalid knowledge_base_id")
        if document_id is not None and not re.fullmatch(r"[0-9a-f]{64}", document_id):
            raise ValueError("Invalid document_id")
        if not self.has_collection():
            return []
        expression = f'knowledge_base_id == "{knowledge_base_id}"'
        if document_id is not None:
            expression += f' and document_id == "{document_id}"'
        iterator = self.client.query_iterator(
            collection_name=self.collection,
            batch_size=1000,
            filter=expression,
            output_fields=["document_id", "document_name", "page_start", "page_end", "section_title"] + (["content"] if include_content else []),
        )
        rows: list[dict[str, Any]] = []
        try:
            while batch := iterator.next():
                rows.extend(batch)
        finally:
            iterator.close()
        return rows

    def list_documents(self, knowledge_base_id: str) -> list[str]:
        documents = {row["document_id"]: row.get("document_name") or "未知文档" for row in self.document_chunks(knowledge_base_id)}
        return sorted(documents.values())

    def delete_document(self, knowledge_base_id: str, document_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", knowledge_base_id) or not re.fullmatch(r"[0-9a-f]{64}", document_id):
            raise ValueError("Invalid document identifier")
        self.client.delete(collection_name=self.collection, filter=f'knowledge_base_id == "{knowledge_base_id}" and document_id == "{document_id}"')
        self.client.flush(collection_name=self.collection)

    def search(self, vector: list[float], limit: int = 10, knowledge_base_id: str | None = None, document_names: tuple[str, ...] | None = None, page_range: tuple[int, int] | None = None) -> list[dict[str, Any]]:
        if knowledge_base_id and not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", knowledge_base_id):
            raise ValueError("Invalid knowledge_base_id")
        expression = f'knowledge_base_id == "{knowledge_base_id}"' if knowledge_base_id else ""
        if document_names is not None:
            if not document_names:
                return []
            scope = "document_name in " + json.dumps(list(document_names), ensure_ascii=False)
            expression = f"{expression} and {scope}" if expression else scope
        if page_range is not None:
            start, end = page_range
            if type(start) is not int or type(end) is not int or not (1 <= start <= end <= 2**63 - 1):
                raise ValueError("Invalid page_range")
            scope = f"page_start <= {end} and page_end >= {start}"
            expression = f"{expression} and {scope}" if expression else scope
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

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from knowledge_rag.chunking import chunk_document
from knowledge_rag.config import Settings
from knowledge_rag.milvus_store import MilvusChunkStore
from knowledge_rag.models import Chunk, ParsedDocument
from knowledge_rag.normalize import normalize_document
from knowledge_rag.ollama import OllamaEmbedder


@dataclass(frozen=True)
class PreparedIngestion:
    document: ParsedDocument
    chunks: tuple[Chunk, ...]
    output_dir: Path


class IngestionPipeline:
    """Deep module for normalization, fallback recovery, chunking, embedding and indexing."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def prepare(
        self,
        source_path: Path,
        knowledge_base_id: str,
        mineru_content_list: Path | None = None,
        persist: bool = True,
    ) -> PreparedIngestion:
        document = normalize_document(source_path, mineru_content_list)
        chunks = chunk_document(
            document,
            knowledge_base_id=knowledge_base_id,
            target_chars=self.settings.chunk_target_chars,
            max_chars=self.settings.chunk_max_chars,
            overlap_chars=self.settings.chunk_overlap_chars,
        )
        output_dir = self.settings.data_dir / "normalized" / document.document_id
        prepared = PreparedIngestion(document, tuple(chunks), output_dir)
        if persist:
            self.save_prepared(prepared)
        return prepared

    @staticmethod
    def save_prepared(prepared: PreparedIngestion) -> None:
        prepared.output_dir.mkdir(parents=True, exist_ok=True)
        (prepared.output_dir / "document.json").write_text(
            json.dumps(asdict(prepared.document), ensure_ascii=False, indent=2),
        )
        chunks_path = prepared.output_dir / "chunks.json"
        incoming = [asdict(chunk) for chunk in prepared.chunks]
        # The same file hash can be indexed in several knowledge bases; do not
        # overwrite another base's cached chunks when importing it again.
        existing = json.loads(chunks_path.read_text()) if chunks_path.exists() else []
        targets = {item["knowledge_base_id"] for item in incoming}
        chunks_path.write_text(json.dumps(
            [item for item in existing if item.get("knowledge_base_id") not in targets] + incoming,
            ensure_ascii=False, indent=2,
        ))

    def embed(self, prepared: PreparedIngestion) -> PreparedIngestion:
        embedder = OllamaEmbedder(
            base_url=self.settings.ollama_base_url,
            model=self.settings.ollama_embed_model,
            vector_dim=self.settings.milvus_vector_dim,
            keep_alive=self.settings.ollama_keep_alive,
        )
        chunks = tuple(embedder.embed_chunks(list(prepared.chunks)))
        (prepared.output_dir / "chunks-with-vectors.json").write_text(
            json.dumps([asdict(chunk) for chunk in chunks], ensure_ascii=False),
        )
        return PreparedIngestion(prepared.document, chunks, prepared.output_dir)

    def index(self, prepared: PreparedIngestion) -> int:
        store = MilvusChunkStore(
            uri=self.settings.milvus_uri,
            collection=self.settings.milvus_collection,
            vector_dim=self.settings.milvus_vector_dim,
        )
        return store.upsert(list(prepared.chunks))

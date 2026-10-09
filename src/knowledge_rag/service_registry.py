from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import Any

from knowledge_rag.admin_store import AdminStore, ModelProfile
from knowledge_rag.config import Settings
from knowledge_rag.milvus_store import MilvusChunkStore
from knowledge_rag.models import Chunk
from knowledge_rag.providers import create_chat_model, create_embedder, create_reranker
from knowledge_rag.rag import RagService


class RagServiceRegistry:
    """Build and cache a service from the currently active server-side profiles."""

    def __init__(self, settings: Settings, store: AdminStore) -> None:
        self.settings = settings
        self.store = store
        self._lock = threading.Lock()
        self._cache_key: tuple[str, ...] | None = None
        self._service: RagService | None = None

    def get(self) -> RagService:
        chat_id = self.store.get_setting("active_chat_profile_id")
        embedding_id = self.store.get_setting("active_embedding_profile_id")
        reranker_id = self.store.get_setting("active_reranker_profile_id")
        collection = self.store.get_setting("active_collection_name", self.settings.milvus_collection)
        vector_dim = int(self.store.get_setting("active_vector_dim", str(self.settings.milvus_vector_dim)))
        chat = self._required_profile(chat_id, "chat")
        embedding = self._required_profile(embedding_id, "embedding")
        reranker = self._required_profile(reranker_id, "reranker") if reranker_id else None
        key = (
            chat.id, chat.updated_at, embedding.id, embedding.updated_at,
            reranker.id if reranker else "", reranker.updated_at if reranker else "",
            collection, str(vector_dim),
        )
        with self._lock:
            if self._service is not None and key == self._cache_key:
                return self._service
            self._service = RagService(
                embedder=create_embedder(embedding),
                store=MilvusChunkStore(self.settings.milvus_uri, collection, vector_dim),
                chat_model=create_chat_model(chat),
                top_k=self.settings.retrieval_top_k,
                min_score=self.settings.retrieval_min_score,
                reranker=create_reranker(reranker) if reranker else None,
                candidate_k=self.settings.retrieval_candidate_k,
                rerank_min_score=self.settings.retrieval_rerank_min_score,
            )
            self._cache_key = key
            return self._service

    def invalidate(self) -> None:
        with self._lock:
            self._cache_key = None
            self._service = None

    def _required_profile(self, profile_id: str, purpose: str) -> ModelProfile:
        profile = self.store.get_profile(profile_id)
        if profile is None or profile.purpose != purpose or not profile.enabled:
            raise RuntimeError(f"当前 {purpose} 模型配置不可用")
        return profile


class EmbeddingIndexManager:
    """Rebuild a Milvus collection off-path and switch it atomically after success."""

    def __init__(self, settings: Settings, store: AdminStore, registry: RagServiceRegistry) -> None:
        self.settings = settings
        self.store = store
        self.registry = registry
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def start(self, profile_id: str, knowledge_base_id: str, actor_id: str) -> str:
        profile = self.store.get_profile(profile_id)
        if profile is None or profile.purpose != "embedding" or not profile.enabled:
            raise ValueError("无效的 Embedding 模型配置")
        if profile.vector_dim is None:
            raise ValueError("请先测试并保存 Embedding 向量维度")
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        collection = f"knowledge_chunks_{profile.id.replace('-', '')[:8]}_{stamp}"
        job = self.store.create_index_job(profile_id, knowledge_base_id, collection, actor_id)
        thread = threading.Thread(target=self._run, args=(job.id,), daemon=True, name=f"embedding-index-{job.id[:8]}")
        with self._lock:
            self._threads[job.id] = thread
        thread.start()
        return job.id

    def _run(self, job_id: str) -> None:
        job = self.store.get_index_job(job_id)
        if job is None:
            return
        try:
            profile = self.store.get_profile(job.profile_id)
            if profile is None or profile.vector_dim is None:
                raise RuntimeError("Embedding 配置已不存在或缺少维度")
            chunks = self._load_chunks(job.knowledge_base_id)
            if not chunks:
                raise RuntimeError("没有找到可重新向量化的 chunks.json")
            self.store.update_index_job(job_id, status="running", total_chunks=len(chunks), completed_chunks=0)
            embedder = create_embedder(profile)
            milvus = MilvusChunkStore(self.settings.milvus_uri, job.collection_name, profile.vector_dim)
            batch_size = max(1, int(profile.options.get("batch_size", 8)))
            completed = 0
            for start in range(0, len(chunks), batch_size):
                batch = chunks[start : start + batch_size]
                vectors = embedder.embed([chunk.content for chunk in batch])
                embedded = [chunk.with_vector(vector) for chunk, vector in zip(batch, vectors, strict=True)]
                milvus.upsert(embedded)
                completed += len(embedded)
                self.store.update_index_job(job_id, completed_chunks=completed)
            self.store.set_setting("active_embedding_profile_id", profile.id)
            self.store.set_setting("active_collection_name", job.collection_name)
            self.store.set_setting("active_vector_dim", str(profile.vector_dim))
            self.registry.invalidate()
            self.store.update_index_job(job_id, status="completed", completed_chunks=completed)
        except Exception as exc:
            self.store.update_index_job(job_id, status="failed", error=str(exc)[:1000])
        finally:
            with self._lock:
                self._threads.pop(job_id, None)

    def _load_chunks(self, knowledge_base_id: str) -> list[Chunk]:
        chunks: list[Chunk] = []
        for path in sorted((self.settings.data_dir / "normalized").glob("*/chunks.json")):
            try:
                payload: list[dict[str, Any]] = json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"无法读取规范化切块 {path.parent.name}/{path.name}") from exc
            for item in payload:
                if item.get("knowledge_base_id") != knowledge_base_id:
                    continue
                item["block_types"] = tuple(item.get("block_types") or ())
                item["vector"] = None
                chunks.append(Chunk(**item))
        return chunks

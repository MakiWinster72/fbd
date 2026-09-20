from __future__ import annotations

import json
import urllib.request
from dataclasses import replace

from knowledge_rag.models import Chunk


class OllamaEmbedder:
    def __init__(self, base_url: str, model: str, vector_dim: int, keep_alive: int = 0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.vector_dim = vector_dim
        self.keep_alive = keep_alive
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        request = urllib.request.Request(
            f"{self.base_url}/api/embed",
            data=json.dumps(
                {"model": self.model, "input": texts, "keep_alive": self.keep_alive},
                ensure_ascii=False,
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self._opener.open(request, timeout=900) as response:
            payload = json.load(response)
        embeddings = payload.get("embeddings") or []
        if len(embeddings) != len(texts):
            raise RuntimeError(f"Ollama returned {len(embeddings)} embeddings for {len(texts)} inputs")
        for vector in embeddings:
            if len(vector) != self.vector_dim:
                raise RuntimeError(f"Embedding dimension {len(vector)} does not match expected {self.vector_dim}")
        return embeddings

    def embed_chunks(self, chunks: list[Chunk], batch_size: int = 8) -> list[Chunk]:
        output: list[Chunk] = []
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors = self.embed([chunk.content for chunk in batch])
            output.extend(replace(chunk, vector=tuple(vector)) for chunk, vector in zip(batch, vectors, strict=True))
        return output


class OllamaChatModel:
    def __init__(self, base_url: str, model: str, keep_alive: int = 0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.keep_alive = keep_alive
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(
                {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                    "think": False,
                    "keep_alive": self.keep_alive,
                    "options": {
                        "temperature": 0.1,
                        "num_ctx": 8192,
                        "num_predict": 512,
                    },
                },
                ensure_ascii=False,
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self._opener.open(request, timeout=900) as response:
            payload = json.load(response)
        content = payload.get("message", {}).get("content", "").strip()
        if not content:
            raise RuntimeError("Ollama returned an empty chat response")
        return content

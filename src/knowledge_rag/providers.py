from __future__ import annotations

import ipaddress
import json
import math
import re
import urllib.error
import urllib.request
from contextlib import suppress
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from knowledge_rag.admin_store import ModelProfile
from knowledge_rag.models import Chunk
from knowledge_rag.ollama import OllamaChatModel, OllamaEmbedder

_BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal"}


def validate_base_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Base URL 必须是有效的 http(s) 地址，且不能包含用户名或密码")
    if parsed.hostname.lower() in _BLOCKED_HOSTS:
        raise ValueError("该 Base URL 被安全策略禁止")
    try:
        address = ipaddress.ip_address(parsed.hostname)
        if address.is_link_local or address.is_multicast or address.is_unspecified:
            raise ValueError("该 Base URL 指向受限网络地址")
    except ValueError as exc:
        if "受限" in str(exc):
            raise
    return value


def validate_profile_protocol(purpose: str, protocol: str, base_url: str) -> None:
    """Reject incompatible endpoint families for known hosted providers."""
    host = (urlsplit(base_url).hostname or "").lower()
    if host == "api.siliconflow.cn":
        expected = "jina_compatible" if purpose == "reranker" else "openai_compatible"
        if protocol != expected:
            raise ValueError(f"SiliconFlow 的 {purpose} 模型应使用 {expected} 协议，而不是 {protocol}")


class OpenAICompatibleClient:
    def __init__(self, profile: ModelProfile) -> None:
        self.base_url = validate_base_url(profile.base_url)
        self.api_key = profile.api_key
        self.model = profile.model_name
        self.options = profile.options
        self.vector_dim = profile.vector_dim
        hostname = urlsplit(self.base_url).hostname or ""
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = hostname.lower() == "localhost"
        # Never route a local model service through HTTP(S)_PROXY.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if is_loopback else urllib.request.build_opener()

    def _post(self, path: str, payload: dict[str, Any], timeout: int = 180) -> Any:
        """Return decoded JSON; TEI endpoints may return a top-level array."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers=headers,
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode(errors="replace")
            raise RuntimeError(f"模型服务返回 HTTP {exc.code}: {detail}") from exc


class OpenAICompatibleChatModel(OpenAICompatibleClient):
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
        }
        for key in ("temperature", "max_tokens", "top_p"):
            if key in self.options:
                payload[key] = self.options[key]
        response = self._post("/chat/completions", payload)
        choices = response.get("choices") or []
        content = choices[0].get("message", {}).get("content", "").strip() if choices else ""
        if not content:
            raise RuntimeError("模型服务返回了空回答")
        return content


class JinaCompatibleReranker(OpenAICompatibleClient):
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        response = self._post(
            "/rerank",
            {
                "model": self.model,
                "query": query,
                "documents": documents,
                "top_n": len(documents),
                "return_documents": False,
            },
            timeout=int(self.options.get("timeout", 180)),
        )
        results = response.get("results") or response.get("data") or []
        scores: list[float | None] = [None] * len(documents)
        for result in results:
            index = int(result.get("index", -1))
            if 0 <= index < len(scores):
                scores[index] = float(result.get("relevance_score", result.get("score", 0.0)))
        if any(score is None for score in scores):
            raise RuntimeError("Reranker 返回的结果缺少候选文档")
        return [float(score) for score in scores if score is not None]


class TeiReranker(OpenAICompatibleClient):
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        response = self._post(
            "/rerank",
            {"query": query, "texts": documents, "raw_scores": False},
            timeout=int(self.options.get("timeout", 180)),
        )
        results = response if isinstance(response, list) else response.get("results") or []
        scores: list[float | None] = [None] * len(documents)
        for result in results:
            index = int(result.get("index", -1))
            if 0 <= index < len(scores):
                scores[index] = float(result.get("score", result.get("relevance_score", 0.0)))
        if any(score is None for score in scores):
            raise RuntimeError("TEI Reranker 返回的结果缺少候选文档")
        return [float(score) for score in scores if score is not None]


class OllamaReranker(OpenAICompatibleClient):
    """Score Qwen3 reranker pairs through Ollama's native token log probabilities."""

    DEFAULT_INSTRUCTION = (
        "Given a user question, determine whether the document contains information "
        "that directly helps answer it."
    )
    SYSTEM_PROMPT = (
        "Judge whether the Document meets the requirements based on the Query and the "
        'Instruct provided. Note that the answer can only be "yes" or "no".'
    )

    @staticmethod
    def _safe_prompt_value(value: str) -> str:
        # Prevent indexed text from injecting Qwen chat-template control tokens.
        return value.replace("<|", "<\u200b|")

    def _prompt(self, query: str, document: str) -> str:
        instruction = str(self.options.get("instruction") or self.DEFAULT_INSTRUCTION)
        max_document_chars = max(1, int(self.options.get("max_document_chars", 12_000)))
        return (
            f"<|im_start|>system\n{self.SYSTEM_PROMPT}<|im_end|>\n"
            "<|im_start|>user\n"
            f"<Instruct>: {self._safe_prompt_value(instruction)}\n"
            f"<Query>: {self._safe_prompt_value(query)}\n"
            f"<Document>: {self._safe_prompt_value(document[:max_document_chars])}<|im_end|>\n"
            "<|im_start|>assistant\n<think>\n\n</think>\n\n"
        )

    @staticmethod
    def _score_logprobs(response: dict[str, Any]) -> float:
        rows = response.get("logprobs") or []
        if not rows:
            raise RuntimeError("Ollama 未返回 token logprobs；请升级 Ollama 或检查模型能力")
        first = rows[0]
        alternatives = list(first.get("top_logprobs") or [])
        alternatives.append({"token": first.get("token", ""), "logprob": first.get("logprob")})
        class_probabilities = {"yes": 0.0, "no": 0.0}
        seen: set[tuple[str, float]] = set()
        for item in alternatives:
            token = str(item.get("token", "")).strip().lower()
            if token not in class_probabilities or item.get("logprob") is None:
                continue
            logprob = float(item["logprob"])
            key = (token, logprob)
            if key in seen:
                continue
            seen.add(key)
            class_probabilities[token] += math.exp(logprob)
        if not any(class_probabilities.values()):
            output = str(response.get("response", "")).strip()[:80]
            raise RuntimeError(f"Ollama Reranker 未返回 yes/no 候选 token：{output!r}")
        # A class outside top_logprobs is still possible, but its probability is negligible.
        yes_probability = class_probabilities["yes"] or math.exp(-20)
        no_probability = class_probabilities["no"] or math.exp(-20)
        return yes_probability / (yes_probability + no_probability)

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        timeout = int(self.options.get("timeout", 180))
        keep_alive = self.options.get("keep_alive", "5m")
        scores: list[float] = []
        try:
            for document in documents:
                response = self._post(
                    "/api/generate",
                    {
                        "model": self.model,
                        "prompt": self._prompt(query, document),
                        "raw": True,
                        "stream": False,
                        "think": False,
                        "keep_alive": keep_alive,
                        "logprobs": True,
                        "top_logprobs": 20,
                        "options": {"temperature": 0, "num_predict": 1},
                    },
                    timeout=timeout,
                )
                scores.append(self._score_logprobs(response))
        finally:
            # This workstation has 8 GB VRAM. Release the 5 GB reranker before chat generation.
            if self.options.get("unload_after", True):
                # Optional unloading must not mask the reranking result/error.
                with suppress(Exception):
                    self._post(
                        "/api/generate",
                        {"model": self.model, "keep_alive": 0},
                        timeout=timeout,
                    )
        return scores


class OpenAICompatibleEmbedder(OpenAICompatibleClient):
    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, Any] = {"model": self.model, "input": texts}
        if self.options.get("dimensions"):
            payload["dimensions"] = int(self.options["dimensions"])
        response = self._post("/embeddings", payload, timeout=900)
        rows = sorted(response.get("data") or [], key=lambda item: item.get("index", 0))
        vectors = [row.get("embedding") or [] for row in rows]
        if len(vectors) != len(texts) or any(not vector for vector in vectors):
            raise RuntimeError("Embedding 服务返回的向量数量不正确")
        if self.vector_dim is not None and any(len(vector) != self.vector_dim for vector in vectors):
            raise RuntimeError(f"Embedding 维度与配置值 {self.vector_dim} 不一致")
        return vectors

    def embed_chunks(self, chunks: list[Chunk], batch_size: int = 8) -> list[Chunk]:
        output: list[Chunk] = []
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors = self.embed([chunk.content for chunk in batch])
            output.extend(replace(chunk, vector=tuple(vector)) for chunk, vector in zip(batch, vectors, strict=True))
        return output


def create_chat_model(profile: ModelProfile):
    validate_base_url(profile.base_url)
    if profile.protocol == "ollama":
        return OllamaChatModel(
            profile.base_url,
            profile.model_name,
            int(profile.options.get("keep_alive", 0)),
        )
    if profile.protocol == "openai_compatible":
        return OpenAICompatibleChatModel(profile)
    raise ValueError(f"不支持的模型协议：{profile.protocol}")


def create_reranker(profile: ModelProfile):
    validate_base_url(profile.base_url)
    if profile.protocol == "jina_compatible":
        return JinaCompatibleReranker(profile)
    if profile.protocol == "tei":
        return TeiReranker(profile)
    if profile.protocol == "ollama_reranker":
        return OllamaReranker(profile)
    raise ValueError(f"不支持的重排序协议：{profile.protocol}")


def create_embedder(profile: ModelProfile):
    validate_base_url(profile.base_url)
    if profile.vector_dim is None:
        raise ValueError("Embedding 配置缺少向量维度，请先测试连接")
    if profile.protocol == "ollama":
        return OllamaEmbedder(
            profile.base_url,
            profile.model_name,
            profile.vector_dim,
            int(profile.options.get("keep_alive", 0)),
        )
    if profile.protocol == "openai_compatible":
        return OpenAICompatibleEmbedder(profile)
    raise ValueError(f"不支持的模型协议：{profile.protocol}")


def test_profile(profile: ModelProfile) -> dict[str, Any]:
    if profile.purpose == "chat":
        answer = create_chat_model(profile).complete("只回答 OK。", "连通性测试")
        # Never display provider reasoning, including orphan closing tags.
        answer = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", answer, flags=re.IGNORECASE | re.DOTALL)
        closing = list(re.finditer(r"</think\s*>", answer, flags=re.IGNORECASE))
        if closing:
            answer = answer[closing[-1].end():]
        answer = re.split(r"<think\b[^>]*>", answer, maxsplit=1, flags=re.IGNORECASE)[0]
        return {"ok": True, "sample": answer.strip()[:200]}
    if profile.purpose == "reranker":
        scores = create_reranker(profile).rerank(
            "项目预算是多少？",
            ["天气晴朗。", "项目预算为 120 万元。"],
        )
        if len(scores) != 2:
            raise RuntimeError("Reranker 测试返回数量不正确")
        return {"ok": True, "sample": f"scores={','.join(f'{score:.4f}' for score in scores)}"}
    # Allow dimension auto-detection during test, before it has been saved.
    if profile.protocol == "ollama":
        expected = profile.vector_dim or 1
        embedder = OllamaEmbedder(profile.base_url, profile.model_name, expected, int(profile.options.get("keep_alive", 0)))
        if profile.vector_dim is None:
            # OllamaEmbedder validates expected dimensions, so query directly for discovery.
            request = urllib.request.Request(
                f"{validate_base_url(profile.base_url)}/api/embed",
                data=json.dumps({"model": profile.model_name, "input": ["连接测试"], "keep_alive": 0}, ensure_ascii=False).encode(),
                headers={"Content-Type": "application/json"},
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=180) as response:
                payload = json.load(response)
            vectors = payload.get("embeddings") or []
        else:
            vectors = embedder.embed(["连接测试"])
    else:
        vectors = OpenAICompatibleEmbedder(profile).embed(["连接测试"])
    if not vectors or not vectors[0]:
        raise RuntimeError("Embedding 服务未返回向量")
    return {"ok": True, "dimensions": len(vectors[0])}

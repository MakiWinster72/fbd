from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Protocol


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SearchStore(Protocol):
    def search(
        self,
        vector: list[float],
        limit: int = 10,
        knowledge_base_id: str | None = None,
    ) -> list[dict[str, Any]]: ...


class ChatModel(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str: ...


class Reranker(Protocol):
    def rerank(self, query: str, documents: list[str]) -> list[float]: ...


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievedSource:
    index: int
    score: float
    document_id: str
    document_name: str
    page_start: int
    page_end: int
    section_title: str
    excerpt: str
    content: str
    vector_score: float = 0.0
    rerank_score: float | None = None


@dataclass(frozen=True)
class ChatResult:
    answer: str
    grounded: bool
    sources: tuple[RetrievedSource, ...]


SYSTEM_PROMPT = """你是私有文档知识库助手。
必须严格遵守以下规则：
1. 只能依据用户消息中提供的“检索证据”回答，不得使用外部知识补充事实。
2. 每个事实结论后使用【1】、【2】格式标注对应证据编号。
3. 不得编造文件名、页码、数字、人名、项目或引用编号。
4. 多份证据冲突时，明确说明冲突并分别引用。
5. 证据不足时直接回答“当前知识库中没有足够依据回答该问题”。
6. 回答使用简体中文，先给结论，再给必要说明。
"""


class RagService:
    """Retrieve evidence, enforce score thresholds, and produce cited answers."""

    def __init__(
        self,
        embedder: Embedder,
        store: SearchStore,
        chat_model: ChatModel,
        top_k: int = 6,
        min_score: float = 0.35,
        reranker: Reranker | None = None,
        candidate_k: int = 20,
    ) -> None:
        self.embedder = embedder
        self.store = store
        self.chat_model = chat_model
        self.top_k = top_k
        self.min_score = min_score
        self.reranker = reranker
        self.candidate_k = max(top_k, candidate_k)

    def retrieve(self, question: str, knowledge_base_id: str) -> tuple[RetrievedSource, ...]:
        vector = self.embedder.embed([question])[0]
        search_limit = self.candidate_k if self.reranker is not None else self.top_k
        hits = self.store.search(vector, limit=search_limit, knowledge_base_id=knowledge_base_id)
        candidates: list[RetrievedSource] = []
        for hit in hits:
            entity = hit.get("entity") or hit
            score = float(hit.get("distance", hit.get("score", 0.0)))
            if score < self.min_score:
                continue
            content = str(entity.get("content") or "").strip()
            if not content:
                continue
            candidates.append(
                RetrievedSource(
                    index=len(candidates) + 1,
                    score=score,
                    document_id=str(entity.get("document_id") or ""),
                    document_name=str(entity.get("document_name") or "未知文档"),
                    page_start=int(entity.get("page_start") or 1),
                    page_end=int(entity.get("page_end") or entity.get("page_start") or 1),
                    section_title=str(entity.get("section_title") or ""),
                    excerpt=_excerpt_from_content(content),
                    content=content,
                    vector_score=score,
                )
            )
        if not candidates:
            return ()
        if self.reranker is None:
            return tuple(replace(source, index=index) for index, source in enumerate(candidates[: self.top_k], 1))
        try:
            rerank_scores = self.reranker.rerank(question, [source.content for source in candidates])
            if len(rerank_scores) != len(candidates):
                raise RuntimeError(f"Reranker returned {len(rerank_scores)} scores for {len(candidates)} documents")
            ranked = sorted(zip(candidates, rerank_scores, strict=True), key=lambda item: item[1], reverse=True)
            return tuple(
                replace(source, index=index, score=float(rerank_score), rerank_score=float(rerank_score))
                for index, (source, rerank_score) in enumerate(ranked[: self.top_k], 1)
            )
        except Exception as exc:
            logger.warning("Reranker failed; falling back to vector order: %s", exc)
            return tuple(replace(source, index=index) for index, source in enumerate(candidates[: self.top_k], 1))

    def answer(self, question: str, knowledge_base_id: str) -> ChatResult:
        sources = self.retrieve(question, knowledge_base_id)
        if not sources:
            return ChatResult(
                answer="当前知识库中没有足够依据回答该问题。",
                grounded=False,
                sources=(),
            )
        evidence = "\n\n".join(
            f"【{source.index}】文件：《{source.document_name}》；页码：{_page_label(source)}"
            + (f"；章节：{source.section_title}" if source.section_title else "")
            + f"\n{source.content}"
            for source in sources
        )
        prompt = f"问题：{question}\n\n检索证据：\n{evidence}"
        answer = self.chat_model.complete(SYSTEM_PROMPT, prompt)
        answer = _remove_invalid_citations(answer, len(sources))
        return ChatResult(answer=answer, grounded=True, sources=sources)


def _excerpt_from_content(content: str) -> str:
    lines = content.splitlines()
    while lines and (
        not lines[0].strip()
        or lines[0].startswith("文档：")
        or lines[0].startswith("章节：")
        or lines[0].startswith("页码：")
    ):
        lines.pop(0)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()[:300]


def _page_label(source: RetrievedSource) -> str:
    if source.page_start == source.page_end:
        return f"第 {source.page_start} 页"
    return f"第 {source.page_start}-{source.page_end} 页"


def _remove_invalid_citations(answer: str, source_count: int) -> str:
    def replace(match: re.Match[str]) -> str:
        return match.group(0) if 1 <= int(match.group(1)) <= source_count else ""

    return re.sub(r"【(\d+)】", replace, answer)

from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Protocol

from knowledge_rag.conversation_context import HistoryTurn, resolve_question


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SearchStore(Protocol):
    def search(
        self,
        vector: list[float],
        limit: int = 10,
        knowledge_base_id: str | None = None,
        document_names: tuple[str, ...] | None = None,
        page_range: tuple[int, int] | None = None,
    ) -> list[dict[str, Any]]: ...

    def list_documents(self, knowledge_base_id: str) -> list[str]: ...


class ChatModel(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str: ...


class Reranker(Protocol):
    def rerank(self, query: str, documents: list[str]) -> list[float]: ...


logger = logging.getLogger(__name__)
REFUSAL_ZH = "当前知识库中没有足够依据回答该问题。"
REFUSAL_EN = "There is not enough evidence in the current knowledge base to answer this question."


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
7. 每个事实只引用直接支持它的最少证据。同一事实重复出现且高排名证据已足够时，不要追加重复引用；不能因片段字段相似就引用，项目编号等标识必须精确匹配。
"""

SYSTEM_PROMPT_EN = """You are an assistant for a private document knowledge base.
Follow these rules strictly:
1. Answer only from the retrieved evidence in the user message; do not add external facts.
2. Cite every factual claim using the corresponding 【1】, 【2】 evidence markers.
3. Do not invent file names, page numbers, figures, names, projects, or citations.
4. If sources conflict, explain the conflict and cite each source.
5. If evidence is insufficient, reply: "There is not enough evidence in the current knowledge base to answer this question."
6. Answer in English. Give the conclusion first, followed by necessary details.
7. Cite the minimum evidence that directly supports each fact. When higher-ranked evidence already suffices for a repeated fact, do not add duplicate citations. Similar fields alone do not justify a citation; project codes and other identifiers must match exactly.
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
        rerank_min_score: float | None = None,
    ) -> None:
        if rerank_min_score is not None and (not math.isfinite(rerank_min_score) or not 0 < rerank_min_score <= 1):
            raise ValueError("rerank_min_score must be a finite probability in (0, 1]")
        self.embedder = embedder
        self.store = store
        self.chat_model = chat_model
        self.top_k = top_k
        self.min_score = min_score
        self.reranker = reranker
        self.candidate_k = max(top_k, candidate_k)
        self.rerank_min_score = rerank_min_score

    def retrieve(self, question: str, knowledge_base_id: str) -> tuple[RetrievedSource, ...]:
        document_names = None
        if _has_document_reference(question):
            document_names = _requested_document_names(question, self.store.list_documents(knowledge_base_id))
            if not document_names or len(document_names) > self.top_k:
                return ()
        page_range = _requested_page_range(question, document_names or ())
        if page_range is not None and (len(document_names or ()) != 1 or not (1 <= page_range[0] <= page_range[1] <= 2**63 - 1)):
            return ()
        vector = self.embedder.embed([question])[0]
        search_limit = self.candidate_k if self.reranker is not None else self.top_k
        if document_names is None:
            hits = self.store.search(vector, limit=search_limit, knowledge_base_id=knowledge_base_id)
        else:
            groups = [self.store.search(vector, limit=search_limit, knowledge_base_id=knowledge_base_id, document_names=(name,), page_range=page_range) for name in document_names]
            # Round-robin candidates keep a long document from starving another requested file.
            hits = [group[rank] for rank in range(search_limit) for group in groups if rank < len(group)]
        rerank_min_score = self.rerank_min_score if self.reranker is not None else None
        candidates: list[RetrievedSource] = []
        for hit in hits:
            entity = hit.get("entity") or hit
            score = float(hit.get("distance", hit.get("score", 0.0)))
            if not math.isfinite(score) or (rerank_min_score is None and score < self.min_score):
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
        if not candidates or (document_names is not None and any(not any(source.document_name == name for source in candidates) for name in document_names)):
            return ()
        if self.reranker is None:
            return _limit_sources(candidates, self.top_k, document_names)
        try:
            # Explicit "respectively" fields need evidence per file, not a single
            # chunk that answers every file's question. Keep the answer prompt intact.
            per_file = _per_file_questions(question, document_names) if rerank_min_score is not None else {}
            if per_file:
                rerank_scores = [0.0] * len(candidates)
                for name, subquestion in per_file.items():
                    positions = [index for index, source in enumerate(candidates) if source.document_name == name]
                    batch_scores = self.reranker.rerank(subquestion, [candidates[index].content for index in positions])
                    if len(batch_scores) != len(positions):
                        raise RuntimeError(f"Reranker returned {len(batch_scores)} scores for {len(positions)} documents")
                    for index, score in zip(positions, batch_scores, strict=True):
                        rerank_scores[index] = score
            else:
                rerank_scores = self.reranker.rerank(question, [source.content for source in candidates])
            if len(rerank_scores) != len(candidates):
                raise RuntimeError(f"Reranker returned {len(rerank_scores)} scores for {len(candidates)} documents")
            scores = [float(score) for score in rerank_scores]
            if any(not math.isfinite(score) for score in scores) or (rerank_min_score is not None and any(not 0 <= score <= 1 for score in scores)):
                raise RuntimeError("Reranker returned invalid relevance scores")
            ranked = sorted(zip(candidates, scores, strict=True), key=lambda item: item[1], reverse=True)
            rescored = [replace(source, score=score, rerank_score=score) for source, score in ranked if rerank_min_score is None or score >= rerank_min_score]
            return _limit_sources(rescored, self.top_k, document_names)
        except Exception as exc:
            logger.warning("Reranker failed; falling back to vector order: %s", exc)
            fallback = [source for source in candidates if source.score >= self.min_score]
            return _limit_sources(fallback, self.top_k, document_names)

    def answer(self, question: str, knowledge_base_id: str, locale: str = "zh-CN", history: tuple[HistoryTurn, ...] = ()) -> ChatResult:
        english = locale == "en"
        original_question = question
        # Explicit inventory requests remain deterministic, even in long conversations.
        if history and not _asks_document_inventory(question):
            resolved = resolve_question(question, history, self.chat_model.complete)
            valid = resolved is not None
            if resolved is not None:
                requested: tuple[str, ...] = ()
                resolved_names: tuple[str, ...] = ()
                # Never let model rewriting broaden explicit current file/page scope.
                if _has_document_reference(question) or _has_document_reference(resolved):
                    available = self.store.list_documents(knowledge_base_id)
                    requested = _requested_document_names(question, available)
                    resolved_names = _requested_document_names(resolved, available)
                if _has_document_reference(question):
                    valid = bool(requested) and requested == resolved_names
                pages = _requested_page_range(question, requested)
                resolved_pages = _requested_page_range(resolved, resolved_names)
                if pages is not None:
                    valid = valid and pages == resolved_pages
                # A page selector must become a real single-file search filter,
                # including a selector inherited from the preceding question.
                if resolved_pages is not None:
                    valid = valid and len(resolved_names) == 1 and 1 <= resolved_pages[0] <= resolved_pages[1] <= 2**63 - 1
            if not valid or resolved is None:
                return ChatResult(
                    "Please specify the subject, document, or page you mean." if english else "请明确要追问的对象、文档或页码。",
                    False, (),
                )
            question = resolved
        if _asks_document_inventory(question):
            documents = self.store.list_documents(knowledge_base_id)
            if not documents:
                return ChatResult("No documents are indexed in this knowledge base." if english else "当前知识库尚无已索引文档。", True, ())
            listing = "\n".join(f"{index}. 《{name}》" for index, name in enumerate(documents, 1))
            heading = f"This knowledge base contains {len(documents)} documents:" if english else f"当前知识库共收录 {len(documents)} 份文档："
            return ChatResult(f"{heading}\n{listing}", True, ())
        sources = self.retrieve(question, knowledge_base_id)
        if not sources:
            return ChatResult(
                answer=REFUSAL_EN if english else REFUSAL_ZH,
                grounded=False,
                sources=(),
            )
        evidence = "\n\n".join(
            f"【{source.index}】文件：《{source.document_name}》；页码：{_page_label(source)}"
            + (f"；章节：{source.section_title}" if source.section_title else "")
            + f"\n{source.content}"
            for source in sources
        )
        prompt = f"问题：{question}\n用户本轮原话：{original_question}\n问题和消解后的指代仅用于定位主题，不是事实证据。\n\n检索证据：\n{evidence}"
        system_prompt = SYSTEM_PROMPT_EN if english else SYSTEM_PROMPT
        answer = self.chat_model.complete(system_prompt, prompt)
        answer = _remove_invalid_citations(answer, len(sources))
        if not re.search(r"【\d+】", answer) or answer.strip().lstrip("#* ").casefold().startswith((REFUSAL_ZH.rstrip("。"), REFUSAL_EN.rstrip(".").casefold())):
            # Retrieved candidates alone do not make a refusal or an uncited answer grounded.
            return ChatResult(REFUSAL_EN if english else REFUSAL_ZH, False, ())
        return ChatResult(answer=answer, grounded=True, sources=sources)


def _has_document_reference(question: str) -> bool:
    return bool(re.search(r"《|[.](?:pdf|docx)\b", question, re.I))


def _requested_document_names(question: str, available: list[str]) -> tuple[str, ...]:
    query = unicodedata.normalize("NFKC", question).casefold()
    names = {unicodedata.normalize("NFKC", name).casefold(): name for name in available}
    references = re.findall(r"《([^》]+)》", query)
    references.extend(re.findall(r'["“]([^"”]+[.](?:pdf|docx))["”]', query))
    remainder = re.sub(r'《[^》]+》|["“][^"”]+[.](?:pdf|docx)["”]', " ", query)
    for name in sorted(names, key=len, reverse=True):
        pattern = r"(?<![\w.])" + re.escape(name) + r"(?![\w.])"
        if re.search(pattern, remainder):
            references.append(name)
            remainder = re.sub(pattern, " ", remainder)
    references.extend(re.findall(r"(?<![\w.])[\w.()\-]+[.](?:pdf|docx)(?![\w.])", remainder))
    requested: list[str] = []
    for reference in references:
        reference = reference.strip()
        matched = [original for normalized, original in names.items() if normalized == reference or re.sub(r"[.](?:pdf|docx)$", "", normalized) == reference]
        if not matched:
            return ()
        for name in matched:
            if name not in requested:
                requested.append(name)
    return tuple(requested)


def _per_file_questions(question: str, document_names: tuple[str, ...] | None) -> dict[str, str]:
    """Split only explicit one-to-one noun fields; never infer missing fields."""
    if not document_names or len(document_names) < 2:
        return {}
    query = unicodedata.normalize("NFKC", question)
    markers = list(re.finditer(r"分别\s*(?:给出|提供|说明|列出)", query))
    english = False
    if len(markers) == 1:
        prefix = query[:markers[0].start()]
        body = query[markers[0].end():].strip().rstrip("。.!?！？")
    elif markers:
        return {}
    else:
        match = re.search(r"\b(?:give|provide|state|list)\s+(.+?),?\s+respectively[.!?]*$", query, re.I)
        if match is None:
            return {}
        english = True
        prefix = query[:match.start()]
        body = match[1].rstrip(", ")
    # English possessives and shared/distributive clauses have ambiguous owner scope.
    if re.search(r"['’]s\b|\bof\b", body, re.I):
        return {}
    if re.search(r"各|每|双方|两者|自身|共同|均|[.](?:pdf|docx)\b|\b(?:each|every|both|their|respective)\b", body, re.I):
        return {}
    normalized_body = body.casefold()
    for name in document_names:
        normalized_name = unicodedata.normalize("NFKC", name).casefold()
        stem = re.sub(r"[.](?:pdf|docx)$", "", normalized_name)
        # A short alias such as B的金额 is an explicit file owner, not a neutral subject.
        if normalized_name in normalized_body or len(stem) >= 4 and stem in normalized_body or f"{stem}的" in normalized_body:
            return {}
    # Only an ordered list of quoted, verified files before the fields is unambiguous.
    if len(re.findall(r"《[^》]+》", prefix)) != len(document_names) or _requested_document_names(prefix, list(document_names)) != document_names:
        return {}
    fields = [field.strip() for field in re.split(r"\s*(?:以及|和|与|、|\band\b)\s*", body, flags=re.I)]
    if len(fields) != len(document_names) or any(not field or re.search(r"[，,;；。!?！？《》]|是否|如何|为什么|\b(?:whether|how|why)\b", field, re.I) for field in fields):
        return {}
    # Preserve only explicit neutral subject modifiers. Never infer a missing owner,
    # and never let such a word override the known file-alias check above.
    if any("的" in field and not re.fullmatch(r"(?:学校|项目|合同|研发设备|设备)的[^的]+", field) for field in fields):
        return {}
    # Bare Chinese conjunctions can occur inside names (e.g. 天和). Only supported
    # field-noun endings may establish a split; unknown syntax uses the original query.
    field_endings = (
        "单位", "机构", "部门", "金额", "总值", "预算", "经费", "名称", "姓名",
        "负责人", "代码", "编号", "型号", "地址", "日期", "时间", "数量", "年份",
        "面积", "收入", "支出", "成本", "比例", "状态", "用途", "类型", "期限",
    )
    # Unit annotations remain verbatim in the model query; only noun validation ignores them.
    validation_fields = [re.sub(r"\((?:元|万元|亿元|人|个|项|台|套|平方米|年|月|日|%)\)$", "", field).rstrip() for field in fields]
    if any(re.search(r"[一-龥]", field) and not field.endswith(field_endings) for field in validation_fields):
        return {}
    if any(not field for field in validation_fields):
        return {}
    english_nouns = {
        "publisher", "authority", "institution", "organization", "department", "owner",
        "name", "code", "number", "address", "date", "time", "year", "count", "quantity",
        "budget", "amount", "value", "total", "funding", "cost", "income", "status", "type",
    }
    if any(not re.search(r"[一-龥]", field) and field.casefold().split()[-1] not in english_nouns for field in validation_fields):
        return {}
    return {
        name: f"According to 《{name}》, what is {field}?" if english else f"《{name}》中的{field}是什么？"
        for name, field in zip(document_names, fields, strict=True)
    }


def _requested_page_range(question: str, document_names: tuple[str, ...]) -> tuple[int, int] | None:
    query = unicodedata.normalize("NFKC", question).casefold()
    # Page-like text in a filename is not a page selector.
    for name in document_names:
        query = query.replace(unicodedata.normalize("NFKC", name).casefold(), " ")
    query = re.sub(r"《[^》]+》", " ", query)
    ranges = []
    for pattern in (r"第\s*(\d+)\s*(?:页\s*)?(?:[-–—~至到]\s*(?:第\s*)?(\d+)\s*)?页", r"\bpages?\s+(\d+)(?:\s*(?:[-–—~]|to|through)\s*(\d+))?\b"):
        for match in re.finditer(pattern, query):
            start = int(match[1])
            ranges.append((start, int(match[2]) if match[2] else start))
    if not ranges:
        return None
    # Multiple distinct selectors are ambiguous; never silently search other pages.
    return ranges[0] if len(set(ranges)) == 1 else (0, 0)


def _limit_sources(sources: list[RetrievedSource], limit: int, document_names: tuple[str, ...] | None) -> tuple[RetrievedSource, ...]:
    if document_names is not None and any(not any(source.document_name == name for source in sources) for name in document_names):
        return ()
    selected: list[RetrievedSource] = []
    if document_names is not None and len(document_names) > 1:
        for source in sources:
            if not any(other.document_name == source.document_name for other in selected):
                selected.append(source)
    selected.extend(source for source in sources if source not in selected)
    return tuple(replace(source, index=index) for index, source in enumerate(selected[:limit], 1))


def _asks_document_inventory(question: str) -> bool:
    if _has_document_reference(question):
        return False
    # Match the entire request: content questions mentioning files are not a catalog.
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", question)).strip().casefold()
    text = re.sub(r"[?!。.! ]+$", "", text)
    english = (
        r"(?:please\s+)?(?:"
        r"(?:what|which|how\s+many)\s+(?:documents|files)"
        r"(?:\s+(?:are(?:\s+(?:there|available|indexed|stored))?|do\s+you\s+have))?"
        r"|(?:list|show(?:\s+me)?)\s+(?:(?:all|the|available|indexed|stored)\s+)*(?:documents|files)"
        r"|(?:documents|files))"
        r"(?:\s+(?:in|from|within)\s+(?:this|the|your)\s+(?:knowledge\s+base|archive|library))?"
    )
    chinese = (
        r"(?:请问|请)?(?:你|您)?(?:的)?(?:列出|展示|显示)?"
        r"(?:(?:当前|这个)?(?:知识库|资料库)(?:里面|里|中)?的?|已入库的?)?"
        r"(?:"
        r"(?:有什么|有哪些|有多少|几份|多少份)(?:文档|文件|材料|资料)?"
        r"|(?:列出|展示|显示)?(?:所有|全部)?(?:文档|文件|材料|资料)(?:有几份|有多少份|几份|多少份|清单|目录|列表)?"
        r"|(?:只有|仅有|就)(?:这|那)?[一二两三四五六七八九十几\d]+(?:份|篇|个)(?:文档|文件|材料|资料)"
        r")(?:吗|呢)?"
    )
    return bool(re.fullmatch(english, text) or re.fullmatch(chinese, text.replace(" ", "")))


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

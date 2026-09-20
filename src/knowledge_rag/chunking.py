from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from knowledge_rag.documents import stable_id
from knowledge_rag.models import Chunk, ContentBlock, ParsedDocument


def _split_text(text: str, max_chars: int) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    parts = [part.strip() for part in re.split(r"(?<=[。！？；\n])", text) if part.strip()]
    output: list[str] = []
    current = ""
    for part in parts:
        if current and len(current) + len(part) > max_chars:
            output.append(current)
            current = ""
        while len(part) > max_chars:
            output.append(part[:max_chars])
            part = part[max_chars:]
        current += part
    if current:
        output.append(current)
    return output


def _block_segments(block: ContentBlock, max_chars: int) -> Iterable[tuple[ContentBlock, str]]:
    for text in _split_text(block.text, max_chars):
        yield block, text


def chunk_document(
    document: ParsedDocument,
    knowledge_base_id: str,
    target_chars: int = 800,
    max_chars: int = 1200,
    overlap_chars: int = 120,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    current: list[tuple[ContentBlock, str]] = []
    current_chars = 0
    current_has_new_content = False

    def flush(carry_overlap: bool = True) -> None:
        nonlocal current, current_chars, current_has_new_content
        if not current:
            return
        pages = [block.page_number for block, _ in current]
        section = next((block.section_path[-1] for block, _ in reversed(current) if block.section_path), "")
        body = "\n\n".join(text for _, text in current).strip()
        prefix = f"文档：{document.file_name}\n"
        if section:
            prefix += f"章节：{section}\n"
        prefix += f"页码：第 {min(pages)}" + (f"-{max(pages)}" if min(pages) != max(pages) else "") + " 页\n\n"
        content = prefix + body
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        index = len(chunks)
        chunks.append(
            Chunk(
                id=stable_id(document.document_id, str(index), content_hash),
                document_id=document.document_id,
                logical_document_id=document.logical_document_id,
                knowledge_base_id=knowledge_base_id,
                document_name=document.file_name,
                page_start=min(pages),
                page_end=max(pages),
                section_title=section,
                chunk_index=index,
                content=content,
                content_hash=content_hash,
                block_types=tuple(dict.fromkeys(block.block_type for block, _ in current)),
            )
        )
        overlap = body[-overlap_chars:].strip() if carry_overlap and overlap_chars else ""
        overlap_source = current[-1][0]
        current = []
        current_chars = 0
        current_has_new_content = False
        if overlap:
            current = [(overlap_source, overlap)]
            current_chars = len(overlap)

    for block in document.blocks:
        if block.block_type in {"image", "header", "footer", "page_number"} or not block.text.strip():
            continue
        for segment_block, text in _block_segments(block, max_chars):
            if current:
                pages = {item.page_number for item, _ in current}
                would_cross_page_limit = len(pages | {segment_block.page_number}) > 2
                would_exceed = current_chars + len(text) > max_chars
                if would_cross_page_limit or would_exceed:
                    flush()
            current.append((segment_block, text))
            current_chars += len(text)
            current_has_new_content = True
            if current_chars >= target_chars:
                flush()
    if current_has_new_content:
        flush(carry_overlap=False)
    return chunks

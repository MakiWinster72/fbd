from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from knowledge_rag.documents import document_identity
from knowledge_rag.models import ContentBlock, PageQuality, ParsedDocument
from knowledge_rag.parsers.docx_native import extract_docx_blocks
from knowledge_rag.parsers.mineru import load_mineru_blocks
from knowledge_rag.parsers.pdf_native import extract_pdf_pages, pdf_page_count


def visible_chars(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def normalize_document(
    source_path: Path,
    mineru_content_list: Path | None = None,
    native_fallback_min_chars: int = 40,
) -> ParsedDocument:
    source_path = source_path.resolve()
    document_id, logical_document_id, file_hash = document_identity(source_path)

    if source_path.suffix.lower() == ".docx":
        blocks = extract_docx_blocks(source_path)
        return ParsedDocument(
            document_id=document_id,
            logical_document_id=logical_document_id,
            file_name=source_path.name,
            file_hash=file_hash,
            page_count=1,
            blocks=tuple(blocks),
            page_quality=(PageQuality(1, 0, sum(visible_chars(b.text) for b in blocks), "docx", "ok", False),),
        )

    if source_path.suffix.lower() != ".pdf":
        raise ValueError(f"Unsupported document type: {source_path.suffix}")

    count = pdf_page_count(source_path)
    native_pages = extract_pdf_pages(source_path, count)
    mineru_blocks = load_mineru_blocks(mineru_content_list) if mineru_content_list else []
    by_page: dict[int, list[ContentBlock]] = defaultdict(list)
    for block in mineru_blocks:
        by_page[block.page_number].append(block)

    merged: list[ContentBlock] = []
    quality: list[PageQuality] = []
    for page_number in range(1, count + 1):
        page_blocks = by_page.get(page_number, [])
        meaningful = [
            block
            for block in page_blocks
            if block.block_type not in {"header", "footer", "page_number"} and visible_chars(block.text) > 0
        ]
        mineru_chars = sum(visible_chars(block.text) for block in meaningful)
        native_text = native_pages.get(page_number, "").strip()
        native_chars = visible_chars(native_text)

        if mineru_chars > 0:
            merged.extend(block for block in page_blocks if visible_chars(block.text) > 0)
            quality.append(PageQuality(page_number, mineru_chars, native_chars, None, "ok", False))
        elif native_chars >= native_fallback_min_chars:
            merged.append(
                ContentBlock(
                    page_number=page_number,
                    block_type="text",
                    text=native_text,
                    source="native_pdf_text",
                    metadata={"fallback_reason": "empty_mineru_page"},
                )
            )
            quality.append(PageQuality(page_number, mineru_chars, native_chars, "native_pdf_text", "fallback", False))
        else:
            quality.append(PageQuality(page_number, mineru_chars, native_chars, None, "review_required", True))

    return ParsedDocument(
        document_id=document_id,
        logical_document_id=logical_document_id,
        file_name=source_path.name,
        file_hash=file_hash,
        page_count=count,
        blocks=tuple(merged),
        page_quality=tuple(quality),
    )

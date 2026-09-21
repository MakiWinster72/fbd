from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_rag.chunking import chunk_document
from knowledge_rag.documents import normalized_document_name
from knowledge_rag.models import ContentBlock, PageQuality, ParsedDocument
from knowledge_rag.normalize import normalize_document
from knowledge_rag.parsers.mineru import table_html_to_text


class DocumentTests(unittest.TestCase):
    def test_normalized_name_removes_format_suffix(self) -> None:
        self.assertEqual(
            normalized_document_name(Path("项目预算表（PDF版本）.pdf")),
            "项目预算表",
        )

    def test_table_html_is_searchable_text(self) -> None:
        value = "<table><tr><td>名称</td><td>型号</td></tr><tr><td>服务器</td><td>Dell</td></tr></table>"
        self.assertEqual(table_html_to_text(value), "名称 | 型号\n服务器 | Dell")


class NormalizeTests(unittest.TestCase):
    def test_empty_mineru_page_uses_native_pdf_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.pdf"
            source.write_bytes(b"pdf")
            blocks = [ContentBlock(1, "text", "第一页内容", source="mineru")]
            with (
                patch("knowledge_rag.normalize.pdf_page_count", return_value=2),
                patch("knowledge_rag.normalize.extract_pdf_pages", return_value={1: "第一页原文", 2: "第二页原生文本足够长" * 5}),
                patch("knowledge_rag.normalize.load_mineru_blocks", return_value=blocks),
            ):
                result = normalize_document(source, Path("content.json"), native_fallback_min_chars=20)
        page_two = [block for block in result.blocks if block.page_number == 2]
        self.assertEqual(len(page_two), 1)
        self.assertEqual(page_two[0].source, "native_pdf_text")
        self.assertEqual(result.page_quality[1].status, "fallback")


class ChunkingTests(unittest.TestCase):
    def test_chunks_keep_page_range_and_do_not_emit_overlap_only_chunk(self) -> None:
        document = ParsedDocument(
            document_id="doc",
            logical_document_id="logical",
            file_name="test.pdf",
            file_hash="hash",
            page_count=2,
            blocks=(
                ContentBlock(1, "text", "甲" * 80),
                ContentBlock(2, "text", "乙" * 80),
            ),
            page_quality=(
                PageQuality(1, 80, 80, None, "ok", False),
                PageQuality(2, 80, 80, None, "ok", False),
            ),
        )
        chunks = chunk_document(document, "kb", target_chars=80, max_chars=100, overlap_chars=10)
        self.assertEqual(len(chunks), 2)
        self.assertEqual((chunks[0].page_start, chunks[0].page_end), (1, 1))
        self.assertEqual((chunks[1].page_start, chunks[1].page_end), (1, 2))
        self.assertIn("乙" * 20, chunks[1].content)


if __name__ == "__main__":
    unittest.main()

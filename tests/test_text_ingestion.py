from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from knowledge_rag.config import Settings
from knowledge_rag.normalize import normalize_document
from knowledge_rag.pipeline import IngestionPipeline
from knowledge_rag.rag import _has_document_reference, _requested_document_names


class TextIngestionTests(unittest.TestCase):
    def test_text_formats_prepare_and_persist_searchable_chunks(self) -> None:
        content = "# 设备清单\n\n固定资产867.23万元。\n\n| 设备 | 数量 |\n| 小型机 | 2 |\n\n```python\nprint('保留代码')\n```"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline = IngestionPipeline(Settings(data_dir=root / "data", chunk_target_chars=70, chunk_max_chars=100, chunk_overlap_chars=10))
            for suffix in (".md", ".txt", ".MD", ".TXT"):
                for encoding in ("utf-8", "utf-8-sig", "utf-16"):
                    with self.subTest(suffix=suffix, encoding=encoding):
                        path = root / f"设备{suffix}"
                        path.write_text(content, encoding=encoding)
                        prepared = pipeline.prepare(path, "private-kb")
                        self.assertEqual(prepared.document.blocks[0].text, content)
                        self.assertEqual(prepared.document.page_count, 1)
                        self.assertFalse(prepared.document.page_quality[0].review_required)
                        self.assertTrue(prepared.chunks)
                        self.assertTrue(all(chunk.knowledge_base_id == "private-kb" for chunk in prepared.chunks))
                        self.assertIn("867.23", "".join(chunk.content for chunk in prepared.chunks))
                        saved = json.loads((prepared.output_dir / "chunks.json").read_text())
                        self.assertEqual(len(saved), len(prepared.chunks))

    def test_empty_binary_and_invalid_encoding_fail_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.txt"
            for content in (b"", b" \n\t", b"hello\x00world", b"\x81\xff"):
                with self.subTest(content=content):
                    path.write_bytes(content)
                    with self.assertRaises(ValueError):
                        normalize_document(path)

    def test_text_file_references_are_scoped_like_pdf(self) -> None:
        available = ["notes.md", "预算.txt"]
        for query, expected in (("notes.md 的设备？", "notes.md"), ('"预算.txt" 的预算？', "预算.txt"), ("《notes》的内容？", "notes.md")):
            with self.subTest(query=query):
                self.assertTrue(_has_document_reference(query))
                self.assertEqual(_requested_document_names(query, available), (expected,))
        self.assertEqual(_requested_document_names("missing.txt 的设备？", available), ())

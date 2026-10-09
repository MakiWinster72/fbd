from __future__ import annotations

import io
import tempfile
import unittest
from http.client import HTTPMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.request import Request

from knowledge_rag.config import Settings
from knowledge_rag.document_import import (
    DocumentImportManager,
    _NoMineruRedirect,
    parse_mineru,
)
from knowledge_rag.index_commit import pending_intents
from knowledge_rag.service_registry import EmbeddingIndexManager


class RecoveryDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_missing_job_after_recovery_update_retains_intent(self) -> None:
        manager = DocumentImportManager.__new__(DocumentImportManager)
        manager.settings = Settings(data_dir=self.root)
        manager.store = MagicMock()
        intent = SimpleNamespace(job_id="missing-job", document_id="a" * 64, clear=MagicMock())
        lease = MagicMock()
        with patch("knowledge_rag.document_import.pending_intents", return_value=[intent]), patch("knowledge_rag.document_import.try_acquire", return_value=lease), patch.object(manager, "get", side_effect=[SimpleNamespace(id="missing-job", status="queued"), None]), patch.object(manager, "update"), patch.object(manager, "_repair_intent") as repair:
            manager.recover_index_intents()
        repair.assert_not_called()
        intent.clear.assert_not_called()
        lease.close.assert_called_once()

    def test_document_summary_keeps_integer_counts_and_page_maximum(self) -> None:
        manager = DocumentImportManager.__new__(DocumentImportManager)
        index = MagicMock()
        index.document_chunks.return_value = [
            {"document_id": "doc", "document_name": "A.pdf", "page_end": 2},
            {"document_id": "doc", "document_name": "A.pdf", "page_end": 5},
        ]
        with patch.object(manager, "_milvus", return_value=index):
            self.assertEqual(manager.documents("default"), [{"document_id": "doc", "document_name": "A.pdf", "chunk_count": 2, "page_count": 5}])

    def test_bad_intent_is_reported_without_deleting_journal(self) -> None:
        journal = self.root / "index-intents" / "broken.json"
        journal.parent.mkdir()
        for contents in ["{broken", "[]", "{}"]:
            with self.subTest(contents=contents):
                journal.write_text(contents)
                with self.assertRaisesRegex(RuntimeError, "index intent"):
                    pending_intents(self.root)
                self.assertEqual(journal.read_text(), contents)

    def test_bad_cache_fails_index_job_without_switching_active_collection(self) -> None:
        chunks = self.root / "normalized" / "doc" / "chunks.json"
        chunks.parent.mkdir(parents=True)
        chunks.write_text("{broken")
        store = MagicMock()
        store.get_index_job.return_value = SimpleNamespace(profile_id="profile", knowledge_base_id="default")
        store.get_profile.return_value = SimpleNamespace(vector_dim=2)
        manager = EmbeddingIndexManager(Settings(data_dir=self.root), store, MagicMock())
        manager._run("job")
        self.assertEqual(store.update_index_job.call_args.kwargs["status"], "failed")
        self.assertIn("无法读取规范化切块", store.update_index_job.call_args.kwargs["error"])
        store.set_setting.assert_not_called()
        self.assertEqual(chunks.read_text(), "{broken")

    def test_bad_document_cache_retains_file_and_reports_error(self) -> None:
        chunks = self.root / "normalized" / ("a" * 64) / "chunks.json"
        chunks.parent.mkdir(parents=True)
        chunks.write_text("{broken")
        manager = DocumentImportManager.__new__(DocumentImportManager)
        manager.settings = Settings(data_dir=self.root)
        with self.assertRaisesRegex(ValueError, "切块"):
            manager._cached_chunks("default", "a" * 64)
        self.assertEqual(chunks.read_text(), "{broken")

    def test_mineru_rejects_unsafe_url_before_reading_or_uploading(self) -> None:
        for url in ["file:///tmp/data", "ftp://localhost", "http:///missing", "https://user:pass@localhost", "http://localhost?token=x", "http://localhost#fragment"]:
            with self.subTest(url=url):
                with patch("knowledge_rag.document_import.urllib.request.build_opener") as opener, self.assertRaisesRegex(ValueError, "HTTP"):
                    parse_mineru(self.root / "missing.pdf", self.root / "output", url, False)
                opener.assert_not_called()

    def test_mineru_bad_json_is_reported_before_writing_output(self) -> None:
        source = self.root / "source.pdf"
        source.write_bytes(b"fixture")
        output = self.root / "output"
        for responses in [
            [b"{broken"],
            [b'["uploaded.pdf"]', b"{broken"],
            [b'["uploaded.pdf"]', b'{"event_id":"event"}', b"event: complete\ndata: {broken\n"],
        ]:
            with self.subTest(responses=responses):
                opener = MagicMock()
                opener.open.side_effect = [io.BytesIO(response) for response in responses]
                with patch("knowledge_rag.document_import.urllib.request.build_opener", return_value=opener), self.assertRaisesRegex(RuntimeError, "无效结果"):
                    parse_mineru(source, output, "http://127.0.0.1:7860", False)
                self.assertFalse(output.exists())
                self.assertEqual(source.read_bytes(), b"fixture")

    def test_mineru_does_not_follow_redirects(self) -> None:
        handler = _NoMineruRedirect()
        request = Request("http://127.0.0.1:7860")
        self.assertIsNone(handler.redirect_request(request, io.BytesIO(), 302, "Found", HTTPMessage(), "file:///tmp/private"))

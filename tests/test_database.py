from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from knowledge_rag.admin_store import AdminStore


class DatabaseMigrationTests(unittest.TestCase):
    def test_legacy_sqlite_model_profile_checks_are_upgraded_for_reranker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE model_profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    purpose TEXT NOT NULL CHECK (purpose IN ('chat', 'embedding')),
                    protocol TEXT NOT NULL CHECK (protocol IN ('ollama', 'openai_compatible')),
                    base_url TEXT NOT NULL,
                    api_key_ciphertext TEXT NOT NULL DEFAULT '',
                    model_name TEXT NOT NULL,
                    vector_dim INTEGER,
                    options_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO model_profiles VALUES (
                    'legacy-chat', '旧对话模型', 'chat', 'ollama', 'http://127.0.0.1:11434', '',
                    'qwen3.5:9b', NULL, '{}', 1, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
                );
                """
            )
            connection.commit()
            connection.close()

            store = AdminStore(path, "migration-test-secret")
            reranker = store.create_profile(
                name="重排序模型",
                purpose="reranker",
                protocol="tei",
                base_url="http://127.0.0.1:8081",
                api_key="",
                model_name="BAAI/bge-reranker-v2-m3",
                vector_dim=None,
                options={},
                actor_id="migration-test",
            )

            self.assertEqual(reranker.purpose, "reranker")
            self.assertEqual({profile.id for profile in store.list_profiles()}, {"legacy-chat", reranker.id})


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_rag.config import Settings


class SettingsTests(unittest.TestCase):
    def test_independent_rerank_threshold_loads_from_environment(self) -> None:
        with patch("knowledge_rag.config.load_dotenv"), patch.dict(os.environ, {"RAG_RETRIEVAL_RERANK_MIN_SCORE": "0.8"}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.retrieval_rerank_min_score, 0.8)
        self.assertEqual(settings.retrieval_min_score, 0.35)
        self.assertIsNone(Settings().retrieval_rerank_min_score)

    def test_independent_rerank_threshold_requires_finite_probability(self) -> None:
        for threshold in [0.0, -0.1, 1.1, float("nan"), float("inf")]:
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                Settings(retrieval_rerank_min_score=threshold)

    def test_unset_or_blank_rerank_threshold_uses_ranking_only(self) -> None:
        for environment in [{}, {"RAG_RETRIEVAL_RERANK_MIN_SCORE": ""}, {"RAG_RETRIEVAL_RERANK_MIN_SCORE": "  "}]:
            with self.subTest(environment=environment):
                with patch("knowledge_rag.config.load_dotenv"), patch.dict(os.environ, environment, clear=True):
                    settings = Settings.from_env()
                self.assertIsNone(settings.retrieval_rerank_min_score)
                self.assertEqual(settings.retrieval_min_score, 0.35)

    def test_from_env_loads_dotenv_from_launch_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, ".env").write_text("RAG_MODEL_SECRET_KEY=local-master-secret\n")
            previous_cwd = Path.cwd()
            try:
                os.chdir(directory)
                with patch.dict(os.environ, {}, clear=True):
                    settings = Settings.from_env()
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(settings.model_secret_key, "local-master-secret")

    def test_exported_environment_takes_precedence_over_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, ".env").write_text("RAG_MODEL_SECRET_KEY=dotenv-secret\n")
            previous_cwd = Path.cwd()
            try:
                os.chdir(directory)
                with patch.dict(os.environ, {"RAG_MODEL_SECRET_KEY": "exported-secret"}, clear=True):
                    settings = Settings.from_env()
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(settings.model_secret_key, "exported-secret")


if __name__ == "__main__":
    unittest.main()

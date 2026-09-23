from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_rag.config import Settings


class SettingsTests(unittest.TestCase):
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

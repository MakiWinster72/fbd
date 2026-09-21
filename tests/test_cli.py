from __future__ import annotations

import unittest
from unittest.mock import patch

from knowledge_rag.cli import main


class CliTests(unittest.TestCase):
    @patch("knowledge_rag.cli.uvicorn.run")
    def test_serve_uses_current_package_path(self, run) -> None:
        exit_code = main(["serve", "--host", "0.0.0.0", "--port", "9000", "--reload"])

        self.assertEqual(exit_code, 0)
        run.assert_called_once_with(
            "knowledge_rag.api:app",
            host="0.0.0.0",
            port=9000,
            reload=True,
        )


if __name__ == "__main__":
    unittest.main()

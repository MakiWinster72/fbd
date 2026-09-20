from __future__ import annotations

import re
import subprocess
from pathlib import Path


def pdf_page_count(path: Path) -> int:
    result = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, check=True)
    match = re.search(r"^Pages:\s+(\d+)", result.stdout, re.MULTILINE)
    if not match:
        raise RuntimeError(f"Unable to determine page count: {path}")
    return int(match.group(1))


def extract_pdf_pages(path: Path, page_count: int | None = None) -> dict[int, str]:
    count = page_count or pdf_page_count(path)
    pages: dict[int, str] = {}
    for page_number in range(1, count + 1):
        result = subprocess.run(
            ["pdftotext", "-f", str(page_number), "-l", str(page_number), "-layout", str(path), "-"],
            capture_output=True,
            text=True,
            check=True,
        )
        text = re.sub(r"[ \t]+\n", "\n", result.stdout)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        pages[page_number] = text
    return pages

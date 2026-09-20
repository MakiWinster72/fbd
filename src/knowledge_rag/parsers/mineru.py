from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from knowledge_rag.models import ContentBlock


BLOCK_TYPE_MAP = {
    "text": "text",
    "table": "table",
    "image": "image",
    "header": "header",
    "footer": "footer",
    "page_number": "page_number",
}


def table_html_to_text(value: str) -> str:
    rows: list[str] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", value, re.DOTALL | re.IGNORECASE):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.DOTALL | re.IGNORECASE)
        cleaned = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip() for cell in cells]
        if any(cleaned):
            rows.append(" | ".join(cleaned))
    return "\n".join(rows)


def load_mineru_blocks(path: Path) -> list[ContentBlock]:
    raw: list[dict[str, Any]] = json.loads(path.read_text())
    blocks: list[ContentBlock] = []
    current_sections: list[str] = []
    for item in raw:
        item_type = item.get("type", "text")
        text = item.get("text") or ""
        if item_type == "table":
            text = table_html_to_text(item.get("table_body") or "")
        text = text.strip()
        text_level = item.get("text_level")
        if item_type == "text" and isinstance(text_level, int) and text:
            level = max(1, min(text_level, 6))
            current_sections[level - 1 :] = [text]
            block_type = "title"
        else:
            block_type = BLOCK_TYPE_MAP.get(item_type, "text")
        bbox = item.get("bbox")
        blocks.append(
            ContentBlock(
                page_number=int(item.get("page_idx", 0)) + 1,
                block_type=block_type,
                text=text,
                bbox=tuple(bbox) if isinstance(bbox, list) and len(bbox) == 4 else None,
                section_path=tuple(current_sections),
                source="mineru",
                metadata={
                    "img_path": item.get("img_path"),
                    "text_level": text_level,
                },
            )
        )
    return blocks

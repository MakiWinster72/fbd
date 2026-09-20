from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from knowledge_rag.models import ContentBlock


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _node_text(node: ET.Element) -> str:
    return re.sub(r"\s+", " ", "".join((item.text or "") for item in node.iter(W + "t"))).strip()


def extract_docx_blocks(path: Path) -> list[ContentBlock]:
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))

    blocks: list[ContentBlock] = []
    body = root.find(W + "body")
    if body is None:
        return blocks

    for node in body:
        if node.tag == W + "p":
            text = _node_text(node)
            if text:
                blocks.append(ContentBlock(page_number=1, block_type="text", text=text, source="docx"))
        elif node.tag == W + "tbl":
            rows: list[str] = []
            for tr in node.findall(W + "tr"):
                cells = [_node_text(tc) for tc in tr.findall(W + "tc")]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                blocks.append(ContentBlock(page_number=1, block_type="table", text="\n".join(rows), source="docx"))
    return blocks

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


BlockType = Literal["title", "text", "table", "image", "header", "footer", "page_number"]
QualityStatus = Literal["ok", "fallback", "review_required"]


@dataclass(frozen=True)
class ContentBlock:
    page_number: int
    block_type: BlockType
    text: str
    bbox: tuple[float, float, float, float] | None = None
    section_path: tuple[str, ...] = ()
    source: str = "mineru"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PageQuality:
    page_number: int
    mineru_chars: int
    native_text_chars: int
    fallback_used: str | None
    status: QualityStatus
    review_required: bool


@dataclass(frozen=True)
class ParsedDocument:
    document_id: str
    logical_document_id: str
    file_name: str
    file_hash: str
    page_count: int
    blocks: tuple[ContentBlock, ...]
    page_quality: tuple[PageQuality, ...]


@dataclass(frozen=True)
class Chunk:
    id: str
    document_id: str
    logical_document_id: str
    knowledge_base_id: str
    document_name: str
    page_start: int
    page_end: int
    section_title: str
    chunk_index: int
    content: str
    content_hash: str
    block_types: tuple[str, ...]
    vector: tuple[float, ...] | None = None

    def with_vector(self, vector: list[float]) -> "Chunk":
        return Chunk(**{**asdict(self), "vector": tuple(vector)})

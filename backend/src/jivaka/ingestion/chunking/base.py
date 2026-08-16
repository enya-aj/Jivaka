import re
from abc import ABC, abstractmethod

from jivaka.ingestion.models import Chunk, DocType

_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


class Chunker(ABC):
    source_type: DocType

    @abstractmethod
    def chunk(self, doc_id: str, text: str) -> list[Chunk]:
        ...


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def group_by_max_chars(paragraphs: list[str], max_chars: int) -> list[str]:
    """Greedily groups paragraphs into blocks up to max_chars, splitting an
    over-long paragraph on sentence boundaries rather than mid-sentence."""
    groups: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            groups.append("\n\n".join(current))
            current = []
            current_len = 0

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            flush()
            groups.extend(_split_long_paragraph(paragraph, max_chars))
            continue
        if current_len + len(paragraph) > max_chars and current:
            flush()
        current.append(paragraph)
        current_len += len(paragraph)

    flush()
    return groups


def _split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    sentences = _SENTENCE_BOUNDARY_RE.split(paragraph)
    blocks: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > max_chars:
            blocks.append(current.strip())
            current = ""
        current = f"{current} {sentence}".strip()
    if current:
        blocks.append(current.strip())
    return blocks


def to_chunks(doc_id: str, blocks: list[str], source_type: DocType) -> list[Chunk]:
    return [
        Chunk(doc_id=doc_id, text=block, order=order, source_type=source_type)
        for order, block in enumerate(blocks)
    ]

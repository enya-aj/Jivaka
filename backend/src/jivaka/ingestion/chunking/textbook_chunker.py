from jivaka.ingestion.chunking.base import (
    Chunker,
    group_by_max_chars,
    split_paragraphs,
    to_chunks,
)
from jivaka.ingestion.models import Chunk

MAX_CHUNK_CHARS = 1500


class TextbookChunker(Chunker):
    source_type = "textbook"

    def __init__(self, max_chunk_chars: int = MAX_CHUNK_CHARS):
        self.max_chunk_chars = max_chunk_chars

    def chunk(self, doc_id: str, text: str) -> list[Chunk]:
        paragraphs = split_paragraphs(text)
        blocks = group_by_max_chars(paragraphs, self.max_chunk_chars)
        return to_chunks(doc_id, blocks, self.source_type)

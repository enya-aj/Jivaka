from jivaka.ingestion.chunking.base import Chunker, group_by_max_chars, to_chunks
from jivaka.ingestion.models import Chunk

MAX_CHUNK_CHARS = 1200


class TranscriptChunker(Chunker):
    """Doctor-patient conversation transcripts: a turn is usually one line
    ("Doctor: ...", "หมอ: ..."), so consecutive turns are grouped into
    chunks without ever cutting a turn in half (unless a single turn is
    longer than a chunk)."""

    source_type = "patient_transcript"

    def __init__(self, max_chunk_chars: int = MAX_CHUNK_CHARS):
        self.max_chunk_chars = max_chunk_chars

    def chunk(self, doc_id: str, text: str) -> list[Chunk]:
        turns = [line.strip() for line in text.splitlines() if line.strip()]
        blocks = [block.replace("\n\n", "\n") for block in group_by_max_chars(turns, self.max_chunk_chars)]
        return to_chunks(doc_id, blocks, self.source_type)

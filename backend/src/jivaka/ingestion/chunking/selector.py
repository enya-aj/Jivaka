from typing import Optional

from jivaka.ingestion.chunking.base import Chunker
from jivaka.ingestion.chunking.patient_record_chunker import PatientRecordChunker
from jivaka.ingestion.chunking.textbook_chunker import TextbookChunker
from jivaka.ingestion.models import DocType

_CHUNKERS: dict[DocType, type[Chunker]] = {
    "patient_record": PatientRecordChunker,
    "textbook": TextbookChunker,
}


def get_chunker(doc_type: DocType, max_chunk_chars: Optional[int] = None) -> Chunker:
    """Deterministic, LLM-free chunker for the doc type. max_chunk_chars
    overrides the chunker's default size."""
    try:
        chunker_cls = _CHUNKERS[doc_type]
    except KeyError as exc:
        raise ValueError(f"No chunker registered for doc_type={doc_type!r}") from exc
    return chunker_cls() if max_chunk_chars is None else chunker_cls(max_chunk_chars=max_chunk_chars)

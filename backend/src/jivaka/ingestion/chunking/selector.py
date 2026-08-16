from jivaka.ingestion.chunking.base import Chunker
from jivaka.ingestion.chunking.patient_record_chunker import PatientRecordChunker
from jivaka.ingestion.chunking.textbook_chunker import TextbookChunker
from jivaka.ingestion.models import DocType

_CHUNKERS: dict[DocType, type[Chunker]] = {
    "patient_record": PatientRecordChunker,
    "textbook": TextbookChunker,
}


def get_chunker(doc_type: DocType) -> Chunker:
    try:
        return _CHUNKERS[doc_type]()
    except KeyError as exc:
        raise ValueError(f"No chunker registered for doc_type={doc_type!r}") from exc

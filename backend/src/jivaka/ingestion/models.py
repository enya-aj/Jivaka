import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

DocType = Literal["patient_record", "textbook", "patient_transcript"]
# Document types about a patient: de-identified before chunking, kept on
# the local Ollama model, and never shown to students.
PATIENT_DOC_TYPES: frozenset[str] = frozenset({"patient_record", "patient_transcript"})


def is_patient_doc(doc_type: object) -> bool:
    return doc_type in PATIENT_DOC_TYPES
EntityType = Literal["symptom", "diagnosis", "treatment", "other"]
ExtractionSource = Literal["llm", "ner"]


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Document(BaseModel):
    id: str = Field(default_factory=new_id)
    filename: str
    doc_type: DocType
    source_hash: str
    ocr_used: bool = False
    ingested_at: datetime = Field(default_factory=utcnow)
    # Who uploaded it (Google account email and name). Empty on documents
    # from before sign-in existed, which count as the admin's.
    ingested_by: str = ""
    ingested_by_name: str = ""
    # Hash of the normalized extracted text (see ingestion/fingerprint.py),
    # so the same text under another filename is caught as a duplicate.
    content_hash: str = ""


class Chunk(BaseModel):
    id: str = Field(default_factory=new_id)
    doc_id: str
    text: str
    order: int
    source_type: DocType
    page_number: Optional[int] = None
    char_span: Optional[tuple[int, int]] = None
    # True when the text has been through jivaka.deid (patient records).
    # Chat only reads patient chunks that carry this flag.
    deidentified: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class Entity(BaseModel):
    id: str = Field(default_factory=new_id)
    doc_id: str
    chunk_id: str
    name: str
    normalized_name: str
    entity_type: EntityType
    extraction_source: ExtractionSource = "llm"
    confidence: Optional[float] = None


class EntityRelation(BaseModel):
    source_entity_id: str
    target_entity_id: str
    relation_type: str


class Definition(BaseModel):
    id: str = Field(default_factory=new_id)
    term: str
    definition_text: str
    source: str = "stub"
    source_id: Optional[str] = None
    semantic_type: Optional[str] = None


class ChunkExtraction(BaseModel):
    """Entities + intra-chunk relations extracted from a single chunk."""

    chunk_id: str
    entities: list[Entity] = Field(default_factory=list)
    relations: list[EntityRelation] = Field(default_factory=list)
    # True when the LLM call failed even after retrying, so the chunk is
    # recorded with no entities.
    failed: bool = False


class IngestionResult(BaseModel):
    document: Document
    chunk_count: int
    entity_count: int
    relation_count: int
    definitions_matched: int
    definitions_missing: int
    ocr_pages_used: int
    elapsed_seconds: float
    # True when this file (same content hash + doc_type) was already fully
    # ingested: `document` is the existing one and nothing new was written.
    duplicate: bool = False
    # Chunks reused from an interrupted earlier run of the same file instead
    # of being extracted again.
    resumed_chunks: int = 0
    # Chunks whose extraction failed after retrying (stored with no entities).
    failed_chunks: int = 0
    # Chunks whose text repeated an earlier chunk in the same run, so its
    # extraction was reused instead of calling the LLM again.
    reused_extractions: int = 0
    # Set when the text matched a document ingested from a different file
    # (same content, other filename/format) rather than the same file.
    duplicate_reason: str = ""

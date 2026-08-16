import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

DocType = Literal["patient_record", "textbook"]
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


class Chunk(BaseModel):
    id: str = Field(default_factory=new_id)
    doc_id: str
    text: str
    order: int
    source_type: DocType
    page_number: Optional[int] = None
    char_span: Optional[tuple[int, int]] = None
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


class IngestionResult(BaseModel):
    document: Document
    chunk_count: int
    entity_count: int
    relation_count: int
    definitions_matched: int
    definitions_missing: int
    ocr_pages_used: int
    elapsed_seconds: float

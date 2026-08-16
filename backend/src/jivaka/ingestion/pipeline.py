import time
from pathlib import Path
from typing import Optional

from falkordb import Graph

from jivaka.config import get_settings
from jivaka.db.schema import ensure_schema
from jivaka.ingestion.chunking.selector import get_chunker
from jivaka.ingestion.extraction.base import EntityExtractor
from jivaka.ingestion.extraction.llm_extractor import LLMEntityExtractor
from jivaka.ingestion.graph_writer import GraphWriter
from jivaka.ingestion.intake import load_document
from jivaka.ingestion.linking.base import DefinitionSource
from jivaka.ingestion.linking.selector import get_definition_source
from jivaka.ingestion.models import Document, DocType, IngestionResult
from jivaka.ingestion.ocr.detector import needs_ocr
from jivaka.ingestion.ocr.easyocr_backend import ocr_image


def ingest_document(
    path: Path,
    doc_type: DocType,
    graph: Graph,
    extractor: Optional[EntityExtractor] = None,
    definition_source: Optional[DefinitionSource] = None,
) -> IngestionResult:
    """Orchestrates intake -> OCR fallback -> chunking -> entity extraction
    -> definition linking -> graph write for a single document."""
    start = time.monotonic()
    settings = get_settings()
    extractor = extractor or LLMEntityExtractor()
    definition_source = definition_source or get_definition_source(settings.definition_source)

    ensure_schema(graph)
    writer = GraphWriter(graph)

    loaded = load_document(path)

    ocr_pages_used = 0
    page_texts: list[str] = []
    for page in loaded.pages:
        text = page.text
        if needs_ocr(text, page.image_area, settings.ocr_text_density_threshold):
            if page.image_bytes is not None:
                text = ocr_image(page.image_bytes)
                ocr_pages_used += 1
        page_texts.append(text)

    full_text = "\n\n".join(page_texts)

    document = Document(
        filename=loaded.filename,
        doc_type=doc_type,
        source_hash=loaded.source_hash,
        ocr_used=ocr_pages_used > 0,
    )
    writer.write_document(document)

    chunker = get_chunker(doc_type)
    chunks = chunker.chunk(document.id, full_text)

    entity_count = 0
    relation_count = 0
    definitions_matched = 0
    definitions_missing = 0

    for chunk in chunks:
        writer.write_chunk(chunk)
        extraction = extractor.extract(chunk)
        definitions_by_entity_id = {
            entity.id: definition_source.lookup(entity.normalized_name, entity.entity_type)
            for entity in extraction.entities
        }
        e_count, r_count, d_matched, d_missing = writer.write_chunk_extraction(
            extraction, definitions_by_entity_id
        )
        entity_count += e_count
        relation_count += r_count
        definitions_matched += d_matched
        definitions_missing += d_missing

    elapsed = time.monotonic() - start

    return IngestionResult(
        document=document,
        chunk_count=len(chunks),
        entity_count=entity_count,
        relation_count=relation_count,
        definitions_matched=definitions_matched,
        definitions_missing=definitions_missing,
        ocr_pages_used=ocr_pages_used,
        elapsed_seconds=elapsed,
    )

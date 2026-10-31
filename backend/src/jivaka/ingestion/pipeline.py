import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from falkordb import Graph

from jivaka.config import Settings, get_settings
from jivaka.db.graph_reader import find_documents_by_hash, get_document_graph
from jivaka.db.schema import ensure_schema
from jivaka.deid import Deidentifier, build_deidentifier
from jivaka.ingestion.chunking.selector import get_chunker
from jivaka.ingestion.extraction.base import EntityExtractor
from jivaka.ingestion.extraction.llm_extractor import LLMEntityExtractor
from jivaka.ingestion.graph_writer import GraphWriter
from jivaka.ingestion.intake import LoadedDocument, load_document
from jivaka.ingestion.linking.base import DefinitionSource
from jivaka.ingestion.linking.selector import get_definition_source
from jivaka.ingestion.models import Document, DocType, IngestionResult
from jivaka.ingestion.ocr.detector import needs_ocr
from jivaka.ingestion.ocr.easyocr_backend import ocr_image


# Serializes ingests of identical content within this process, so two
# concurrent uploads of the same file can't both pass the duplicate check (or
# one delete the other's in-progress document as a leftover).
_hash_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


def ingest_document(
    path: Path,
    doc_type: DocType,
    graph: Graph,
    extractor: Optional[EntityExtractor] = None,
    definition_source: Optional[DefinitionSource] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    deidentifier: Optional[Deidentifier] = None,
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

    with _hash_locks[loaded.source_hash]:
        existing = _find_completed_duplicate(graph, writer, loaded.source_hash, doc_type)
        if existing is not None:
            return _result_from_existing(graph, existing, elapsed=time.monotonic() - start)
        return _ingest_loaded(
            loaded, doc_type, writer, extractor, definition_source, settings, on_progress, start, deidentifier
        )


def _find_completed_duplicate(
    graph: Graph, writer: GraphWriter, source_hash: str, doc_type: DocType
) -> Optional[dict]:
    """Returns an already fully ingested document for this content, if any.
    Documents left incomplete by a failed/interrupted earlier run are deleted
    so the re-upload replaces them instead of sitting next to them."""
    completed = None
    for props in find_documents_by_hash(graph, source_hash, doc_type):
        if props.get("ingestion_complete") is False:
            writer.delete_document(props["id"])
        elif completed is None:
            # Documents from before the completion flag existed have no
            # ingestion_complete property; treat them as complete.
            completed = props
    return completed


def _result_from_existing(graph: Graph, props: dict, elapsed: float) -> IngestionResult:
    document = Document(
        id=props["id"],
        filename=props["filename"],
        doc_type=props["doc_type"],
        source_hash=props["source_hash"],
        ocr_used=props.get("ocr_used", False),
        ingested_at=datetime.fromisoformat(props["ingested_at"]),
    )
    if "chunk_count" in props:
        stats = {
            "chunk_count": props["chunk_count"],
            "entity_count": props["entity_count"],
            "relation_count": props["relation_count"],
            "definitions_matched": props["definitions_matched"],
            "definitions_missing": props["definitions_missing"],
            "ocr_pages_used": props["ocr_pages_used"],
        }
    else:
        # Older documents don't carry stored stats, so count from the graph.
        dump = get_document_graph(graph, props["id"]) or {"chunks": [], "relations": []}
        entities = [e for chunk in dump["chunks"] for e in chunk["entities"]]
        matched = sum(1 for e in entities if e["definition"] is not None)
        stats = {
            "chunk_count": len(dump["chunks"]),
            "entity_count": len(entities),
            "relation_count": len(dump["relations"]),
            "definitions_matched": matched,
            "definitions_missing": len(entities) - matched,
            "ocr_pages_used": 0,
        }
    return IngestionResult(document=document, elapsed_seconds=elapsed, duplicate=True, **stats)


def _ingest_loaded(
    loaded: LoadedDocument,
    doc_type: DocType,
    writer: GraphWriter,
    extractor: EntityExtractor,
    definition_source: DefinitionSource,
    settings: Settings,
    on_progress: Optional[Callable[[int, int], None]],
    start: float,
    deidentifier: Optional[Deidentifier],
) -> IngestionResult:
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

    # Patient records are de-identified before chunking, so the graph (chunk
    # text, extracted entities) never holds names, IDs or other identifiers.
    is_patient_record = doc_type == "patient_record"
    if is_patient_record:
        deidentifier = deidentifier or build_deidentifier(
            settings.deid_use_ner, settings.deid_extra_id_patterns
        )
        full_text = deidentifier.deidentify(full_text)

    document = Document(
        filename=loaded.filename,
        doc_type=doc_type,
        source_hash=loaded.source_hash,
        ocr_used=ocr_pages_used > 0,
    )
    writer.write_document(document)
    if is_patient_record:
        writer.assign_pseudonym(document.id)

    chunker = get_chunker(doc_type)
    chunks = chunker.chunk(document.id, full_text)
    for chunk in chunks:
        chunk.deidentified = is_patient_record

    entity_count = 0
    relation_count = 0
    definitions_matched = 0
    definitions_missing = 0

    for i, chunk in enumerate(chunks, start=1):
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
        if on_progress:
            on_progress(i, len(chunks))

    elapsed = time.monotonic() - start

    result = IngestionResult(
        document=document,
        chunk_count=len(chunks),
        entity_count=entity_count,
        relation_count=relation_count,
        definitions_matched=definitions_matched,
        definitions_missing=definitions_missing,
        ocr_pages_used=ocr_pages_used,
        elapsed_seconds=elapsed,
    )
    writer.mark_document_complete(result)
    return result

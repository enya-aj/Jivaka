import logging
import threading
import uuid
import time
from collections import defaultdict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import closing
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
from jivaka.ingestion.extraction.llm_client import OpenRouterClient, build_extraction_client
from jivaka.ingestion.extraction.llm_extractor import LLMEntityExtractor
from jivaka.ingestion.graph_writer import GraphWriter
from jivaka.ingestion.intake import LoadedDocument, load_document
from jivaka.ingestion.linking.base import DefinitionSource
from jivaka.ingestion.linking.selector import get_definition_source
from jivaka.ingestion.models import (
    Chunk,
    ChunkExtraction,
    Definition,
    Document,
    DocType,
    Entity,
    EntityRelation,
    IngestionResult,
)
from jivaka.ingestion.ocr.detector import needs_ocr
from jivaka.ingestion.ocr.easyocr_backend import ocr_image

logger = logging.getLogger(__name__)


# Serializes ingests of identical content within this process, so two
# concurrent uploads of the same file can't both pass the duplicate check (or
# one delete the other's in-progress document as a leftover).
_hash_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


# Caps LLM extractions running at once across every ingest in this process
# (FastAPI runs each upload's ingest in its own thread), so several uploads
# share the Ollama host instead of each adding INGEST_MAX_CONCURRENCY calls.
# Each provider has its own slots, so an OpenRouter ingest doesn't hold up a
# patient record extracting on Ollama.
_llm_slots: dict[tuple[str, int], threading.BoundedSemaphore] = {}
_llm_slots_lock = threading.Lock()


def _llm_semaphore(provider: str, size: int) -> threading.BoundedSemaphore:
    with _llm_slots_lock:
        key = (provider, size)
        if key not in _llm_slots:
            _llm_slots[key] = threading.BoundedSemaphore(size)
        return _llm_slots[key]


class IngestionCancelled(Exception):
    """Raised from an on_progress/on_document callback to stop an ingest.
    The pipeline deletes the partly written document before re-raising."""


def ingest_document(
    path: Path,
    doc_type: DocType,
    graph: Graph,
    extractor: Optional[EntityExtractor] = None,
    definition_source: Optional[DefinitionSource] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    deidentifier: Optional[Deidentifier] = None,
    on_document: Optional[Callable[[str], None]] = None,
) -> IngestionResult:
    """Orchestrates intake -> OCR fallback -> chunking -> entity extraction
    -> definition linking -> graph write for a single document.

    on_document(doc_id) is called once the Document node is written, and
    on_progress(done, total) after each chunk (with done=0 once chunking is
    finished). Either may raise IngestionCancelled to stop the run."""
    start = time.monotonic()
    settings = get_settings()
    provider, max_concurrency = "ollama", settings.ingest_max_concurrency
    if extractor is None:
        client = build_extraction_client(doc_type)
        if isinstance(client, OpenRouterClient):
            provider, max_concurrency = "openrouter", settings.openrouter_max_concurrency
        extractor = LLMEntityExtractor(client=client)
        logger.info("Extracting %s with %s (%s at once)", path.name, provider, max_concurrency)
    definition_source = definition_source or get_definition_source(settings.definition_source)

    ensure_schema(graph)
    writer = GraphWriter(graph)

    loaded = load_document(path)

    with _hash_locks[loaded.source_hash]:
        existing, interrupted = _find_existing(graph, writer, loaded.source_hash, doc_type)
        if existing is not None:
            return _result_from_existing(graph, existing, elapsed=time.monotonic() - start)
        return _ingest_loaded(
            graph,
            loaded,
            doc_type,
            writer,
            extractor,
            definition_source,
            settings,
            on_progress,
            start,
            deidentifier,
            on_document,
            resume_from=interrupted,
            llm=(provider, max_concurrency),
        )


def _find_existing(
    graph: Graph, writer: GraphWriter, source_hash: str, doc_type: DocType
) -> tuple[Optional[dict], Optional[dict]]:
    """Returns (completed, interrupted) documents for this content.

    `completed` is an already fully ingested document, if any. Otherwise
    `interrupted` is the most recent document left incomplete by a failed or
    killed earlier run, which the new run resumes. Any other incomplete
    copies are deleted so they don't sit next to the resumed one."""
    completed = None
    incomplete: list[dict] = []
    for props in find_documents_by_hash(graph, source_hash, doc_type):
        if props.get("ingestion_complete") is False:
            incomplete.append(props)
        elif completed is None:
            # Documents from before the completion flag existed have no
            # ingestion_complete property; treat them as complete.
            completed = props

    if completed is not None:
        for props in incomplete:
            writer.delete_document(props["id"])
        return completed, None

    interrupted = incomplete[-1] if incomplete else None  # ordered by ingested_at
    for props in incomplete[:-1]:
        writer.delete_document(props["id"])
    return None, interrupted


def _finished_chunks(graph: Graph, doc_id: str) -> tuple[dict[int, dict], list[dict]]:
    """Returns (finished chunks by order, all existing chunks) of a document
    from an interrupted run. A chunk is finished once its extraction was
    fully written. Chunks written before that was recorded carry no flag;
    for those, every chunk but the last one was finished, since chunks are
    processed one after another."""
    rows = graph.query(
        "MATCH (:Document {id: $doc_id})-[:HAS_CHUNK]->(c:Chunk) "
        "RETURN c.id, c.order, c.text, c.extracted ORDER BY c.order",
        params={"doc_id": doc_id},
    ).result_set
    chunks = [{"id": r[0], "order": r[1], "text": r[2], "extracted": r[3]} for r in rows]
    if any(c["extracted"] is not None for c in chunks):
        finished = [c for c in chunks if c["extracted"]]
    else:
        last_order = max((c["order"] for c in chunks), default=-1)
        finished = [c for c in chunks if c["order"] < last_order]
    return {c["order"]: c for c in finished}, chunks


def _chunk_stats(graph: Graph, doc_id: str) -> dict[str, tuple[int, int, int]]:
    """(entities, relations, entities with a definition) per chunk id, for
    counting the chunks a resumed run skips."""
    stats: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    queries = (
        "MATCH (c:Chunk {doc_id: $doc_id})-[:MENTIONS]->(e:Entity) RETURN c.id, count(e)",
        "MATCH (c:Chunk {doc_id: $doc_id})-[:MENTIONS]->(:Entity)-[r:RELATED_TO]->(:Entity) RETURN c.id, count(r)",
        "MATCH (c:Chunk {doc_id: $doc_id})-[:MENTIONS]->(e:Entity)-[:DEFINED_AS]->(:Definition) "
        "RETURN c.id, count(DISTINCT e)",
    )
    for index, cypher in enumerate(queries):
        for chunk_id, count in graph.query(cypher, params={"doc_id": doc_id}).result_set:
            stats[chunk_id][index] = count
    return {chunk_id: tuple(values) for chunk_id, values in stats.items()}


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
    graph: Graph,
    loaded: LoadedDocument,
    doc_type: DocType,
    writer: GraphWriter,
    extractor: EntityExtractor,
    definition_source: DefinitionSource,
    settings: Settings,
    on_progress: Optional[Callable[[int, int], None]],
    start: float,
    deidentifier: Optional[Deidentifier],
    on_document: Optional[Callable[[str], None]] = None,
    resume_from: Optional[dict] = None,
    llm: Optional[tuple[str, int]] = None,
) -> IngestionResult:
    llm = llm or ("ollama", settings.ingest_max_concurrency)
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
    if resume_from is not None:
        # Continue the interrupted run's document rather than starting over.
        document.id = resume_from["id"]
        document.ingested_at = datetime.fromisoformat(resume_from["ingested_at"])
    writer.write_document(document)
    try:
        if on_document:
            on_document(document.id)
        if is_patient_record:
            writer.assign_pseudonym(document.id)

        chunker = get_chunker(doc_type, _chunk_size(settings, doc_type))
        chunks = chunker.chunk(document.id, full_text)
        for chunk in chunks:
            chunk.deidentified = is_patient_record
        if on_progress:
            on_progress(0, len(chunks))

        entity_count = 0
        relation_count = 0
        definitions_matched = 0
        definitions_missing = 0
        resumed_chunks = 0

        finished: dict[int, dict] = {}
        existing_chunks: list[dict] = []
        stats: dict[str, tuple[int, int, int]] = {}
        if resume_from is not None:
            finished, existing_chunks = _finished_chunks(graph, document.id)
            stats = _chunk_stats(graph, document.id)
            # Drop chunks that won't be reused: unfinished ones, and any that
            # no longer match (e.g. the chunking settings changed).
            for old in existing_chunks:
                done = finished.get(old["order"])
                still_matches = (
                    done is not None and old["order"] < len(chunks) and chunks[old["order"]].text == old["text"]
                )
                if not still_matches:
                    finished.pop(old["order"], None)
                    writer.delete_chunk(old["id"])

        todo: list[Chunk] = []
        for chunk in chunks:
            done = finished.get(chunk.order)
            if done is None:
                todo.append(chunk)
                continue
            e_count, r_count, d_matched = stats.get(done["id"], (0, 0, 0))
            entity_count += e_count
            relation_count += r_count
            definitions_matched += d_matched
            definitions_missing += e_count - d_matched
            resumed_chunks += 1
        if on_progress and resumed_chunks:
            on_progress(resumed_chunks, len(chunks))

        failed_chunks = 0
        reused_extractions = 0
        progress = resumed_chunks
        extracted = _extract_concurrently(todo, extractor, definition_source, *llm)
        with closing(extracted):  # stops the workers right away if this loop raises
            for chunk, extraction, definitions, reused in extracted:
                e_count, r_count, d_matched, d_missing = writer.write_extracted_chunk(chunk, extraction, definitions)
                entity_count += e_count
                relation_count += r_count
                definitions_matched += d_matched
                definitions_missing += d_missing
                failed_chunks += extraction.failed
                reused_extractions += reused
                progress += 1
                if on_progress:
                    on_progress(progress, len(chunks))
    except IngestionCancelled:
        writer.delete_document(document.id)
        raise

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
        resumed_chunks=resumed_chunks,
        failed_chunks=failed_chunks,
        reused_extractions=reused_extractions,
    )
    writer.mark_document_complete(result)
    return result


def _chunk_size(settings: Settings, doc_type: DocType) -> int:
    if doc_type == "patient_record":
        return settings.patient_record_chunk_max_chars
    return settings.textbook_chunk_max_chars


_Extracted = tuple[ChunkExtraction, dict[str, Optional[Definition]]]


def _extract_concurrently(
    chunks: list[Chunk],
    extractor: EntityExtractor,
    definition_source: DefinitionSource,
    provider: str,
    max_concurrency: int,
):
    """Yields (chunk, extraction, definitions by entity id, reused) in chunk
    order, while up to max_concurrency LLM extractions run in worker threads.

    Only the caller writes to the graph, so writes stay sequential and a
    chunk is only ever written once its extraction is done. A few chunks are
    queued ahead of the workers so the next call starts the moment one
    finishes, but no more, so memory stays flat on huge documents. Chunks
    whose text repeats an earlier chunk of this run reuse its extraction
    (with fresh entity ids) instead of calling the LLM again.

    If the caller stops early (cancel, error), queued chunks are dropped and
    the extractions still running finish in the background unwritten."""
    max_concurrency = max(1, max_concurrency)
    slots = _llm_semaphore(provider, max_concurrency)

    def work(chunk: Chunk) -> _Extracted:
        with slots:
            extraction = extractor.extract(chunk)
        definitions = {
            entity.id: definition_source.lookup(entity.normalized_name, entity.entity_type)
            for entity in extraction.entities
        }
        return extraction, definitions

    by_text: dict[str, Future] = {}
    pending: deque[tuple[Chunk, Future, bool]] = deque()
    queue_ahead = max_concurrency * 2
    executor = ThreadPoolExecutor(max_workers=max_concurrency, thread_name_prefix="ingest-extract")
    completed = False
    try:
        remaining = iter(chunks)
        while True:
            while len(pending) < queue_ahead:
                chunk = next(remaining, None)
                if chunk is None:
                    break
                earlier = by_text.get(chunk.text)
                if earlier is not None:
                    pending.append((chunk, earlier, True))
                else:
                    future = executor.submit(work, chunk)
                    by_text[chunk.text] = future
                    pending.append((chunk, future, False))
            if not pending:
                break
            chunk, future, reused = pending.popleft()
            extraction, definitions = future.result()
            if reused:
                if extraction.failed:
                    # Don't copy a failure; try this chunk on its own.
                    extraction, definitions = work(chunk)
                    reused = False
                else:
                    extraction, definitions = _copy_extraction(extraction, definitions, chunk)
            elif len(by_text) > 10_000:
                by_text.clear()  # bound memory on very large documents
            yield chunk, extraction, definitions, reused
        completed = True
    finally:
        executor.shutdown(wait=completed, cancel_futures=True)


def _copy_extraction(
    extraction: ChunkExtraction, definitions: dict[str, Optional[Definition]], chunk: Chunk
) -> _Extracted:
    """The same entities and relations for another chunk with identical
    text, under new ids so each chunk keeps its own Entity nodes."""
    new_ids: dict[str, str] = {}
    entities: list[Entity] = []
    new_definitions: dict[str, Optional[Definition]] = {}
    for entity in extraction.entities:
        copy = entity.model_copy(update={"id": str(uuid.uuid4()), "chunk_id": chunk.id, "doc_id": chunk.doc_id})
        new_ids[entity.id] = copy.id
        new_definitions[copy.id] = definitions.get(entity.id)
        entities.append(copy)
    relations = [
        EntityRelation(
            source_entity_id=new_ids[r.source_entity_id],
            target_entity_id=new_ids[r.target_entity_id],
            relation_type=r.relation_type,
        )
        for r in extraction.relations
        if r.source_entity_id in new_ids and r.target_entity_id in new_ids
    ]
    return ChunkExtraction(chunk_id=chunk.id, entities=entities, relations=relations), new_definitions

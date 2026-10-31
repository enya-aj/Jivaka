import os
import uuid
from pathlib import Path

import pytest

from jivaka.ingestion.extraction.base import EntityExtractor
from jivaka.ingestion.linking.stub_dictionary import StubDictionarySource
from jivaka.ingestion.models import Chunk, ChunkExtraction, Entity, EntityRelation
from jivaka.ingestion.pipeline import ingest_document

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class FakeEntityExtractor(EntityExtractor):
    """Deterministic stand-in for the Ollama-backed extractor, so this test
    doesn't require a pulled model - only FalkorDB needs to be reachable."""

    def extract(self, chunk: Chunk) -> ChunkExtraction:
        text_lower = chunk.text.lower()
        entities: list[Entity] = []

        def add(name: str, entity_type: str) -> None:
            entities.append(
                Entity(
                    doc_id=chunk.doc_id,
                    chunk_id=chunk.id,
                    name=name,
                    normalized_name=name,
                    entity_type=entity_type,
                    extraction_source="llm",
                )
            )

        if "fever" in text_lower:
            add("fever", "symptom")
        if "pneumonia" in text_lower:
            add("pneumonia", "diagnosis")
        if "amoxicillin" in text_lower:
            add("amoxicillin", "treatment")

        by_name = {e.normalized_name: e for e in entities}
        relations = []
        if "amoxicillin" in by_name and "pneumonia" in by_name:
            relations.append(
                EntityRelation(
                    source_entity_id=by_name["amoxicillin"].id,
                    target_entity_id=by_name["pneumonia"].id,
                    relation_type="treats",
                )
            )

        return ChunkExtraction(chunk_id=chunk.id, entities=entities, relations=relations)


@pytest.fixture
def graph():
    from falkordb import FalkorDB

    host = os.environ.get("FALKOR_HOST", "localhost")
    port = int(os.environ.get("FALKOR_PORT", "6379"))
    graph_name = f"jivaka_test_{uuid.uuid4().hex[:8]}"
    try:
        db = FalkorDB(host=host, port=port)
        g = db.select_graph(graph_name)
        g.query("RETURN 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"FalkorDB not reachable at {host}:{port} ({exc}). Run `docker compose up falkordb` first.")

    yield g

    try:
        g.delete()
    except Exception:  # noqa: BLE001
        pass


def test_ingest_patient_note_end_to_end(graph):
    path = FIXTURES_DIR / "sample_patient_note.txt"

    result = ingest_document(
        path=path,
        doc_type="patient_record",
        graph=graph,
        extractor=FakeEntityExtractor(),
        definition_source=StubDictionarySource(),
    )

    assert result.chunk_count > 0
    assert result.entity_count == 3  # fever, pneumonia, amoxicillin
    assert result.relation_count == 1  # amoxicillin treats pneumonia
    assert result.definitions_matched >= 1  # "fever" is in the stub dictionary
    assert result.ocr_pages_used == 0  # plain .txt input never needs OCR

    doc_rows = graph.query(
        "MATCH (d:Document {id: $id}) RETURN d", params={"id": result.document.id}
    ).result_set
    assert len(doc_rows) == 1

    chunk_count = graph.query(
        "MATCH (:Document {id: $id})-[:HAS_CHUNK]->(c:Chunk) RETURN count(c)",
        params={"id": result.document.id},
    ).result_set[0][0]
    assert chunk_count == result.chunk_count

    entity_count = graph.query(
        "MATCH (:Chunk {doc_id: $id})-[:MENTIONS]->(e:Entity) RETURN count(e)",
        params={"id": result.document.id},
    ).result_set[0][0]
    assert entity_count == result.entity_count

    definition_count = graph.query(
        "MATCH (:Entity {doc_id: $id})-[:DEFINED_AS]->(def:Definition) RETURN count(def)",
        params={"id": result.document.id},
    ).result_set[0][0]
    assert definition_count == result.definitions_matched


class CountingExtractor(FakeEntityExtractor):
    def __init__(self):
        self.calls = 0

    def extract(self, chunk: Chunk) -> ChunkExtraction:
        self.calls += 1
        return super().extract(chunk)


def _document_count(graph) -> int:
    return graph.query("MATCH (d:Document) RETURN count(d)").result_set[0][0]


def test_reingesting_same_file_reuses_existing_document(graph):
    path = FIXTURES_DIR / "sample_patient_note.txt"
    first = ingest_document(
        path=path,
        doc_type="patient_record",
        graph=graph,
        extractor=FakeEntityExtractor(),
        definition_source=StubDictionarySource(),
    )

    extractor = CountingExtractor()
    second = ingest_document(
        path=path,
        doc_type="patient_record",
        graph=graph,
        extractor=extractor,
        definition_source=StubDictionarySource(),
    )

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.document.id == first.document.id
    assert extractor.calls == 0
    assert _document_count(graph) == 1
    assert (second.chunk_count, second.entity_count, second.relation_count) == (
        first.chunk_count,
        first.entity_count,
        first.relation_count,
    )
    assert second.definitions_matched == first.definitions_matched


def test_same_file_as_different_doc_type_is_ingested_separately(graph):
    path = FIXTURES_DIR / "sample_patient_note.txt"
    kwargs = dict(path=path, graph=graph, extractor=FakeEntityExtractor(), definition_source=StubDictionarySource())

    ingest_document(doc_type="patient_record", **kwargs)
    other = ingest_document(doc_type="textbook", **kwargs)

    assert other.duplicate is False
    assert _document_count(graph) == 2


def test_incomplete_leftover_from_failed_run_is_replaced(graph):
    path = FIXTURES_DIR / "sample_patient_note.txt"

    class FailingExtractor(FakeEntityExtractor):
        def extract(self, chunk: Chunk) -> ChunkExtraction:
            raise RuntimeError("LLM went away")

    with pytest.raises(RuntimeError):
        ingest_document(
            path=path,
            doc_type="patient_record",
            graph=graph,
            extractor=FailingExtractor(),
            definition_source=StubDictionarySource(),
        )
    assert _document_count(graph) == 1  # the partial document from the failed run

    result = ingest_document(
        path=path,
        doc_type="patient_record",
        graph=graph,
        extractor=FakeEntityExtractor(),
        definition_source=StubDictionarySource(),
    )

    assert result.duplicate is False
    assert _document_count(graph) == 1
    leftover_chunks = graph.query(
        "MATCH (c:Chunk) WHERE c.doc_id <> $id RETURN count(c)", params={"id": result.document.id}
    ).result_set[0][0]
    assert leftover_chunks == 0


def test_document_from_before_completion_flag_counts_as_existing(graph):
    path = FIXTURES_DIR / "sample_patient_note.txt"
    first = ingest_document(
        path=path,
        doc_type="patient_record",
        graph=graph,
        extractor=FakeEntityExtractor(),
        definition_source=StubDictionarySource(),
    )
    # Simulate a document written before ingestion_complete/stats existed.
    graph.query(
        "MATCH (d:Document {id: $id}) REMOVE d.ingestion_complete, d.chunk_count, d.entity_count, "
        "d.relation_count, d.definitions_matched, d.definitions_missing, d.ocr_pages_used, d.elapsed_seconds",
        params={"id": first.document.id},
    )

    second = ingest_document(
        path=path,
        doc_type="patient_record",
        graph=graph,
        extractor=FakeEntityExtractor(),
        definition_source=StubDictionarySource(),
    )

    assert second.duplicate is True
    assert second.document.id == first.document.id
    assert second.entity_count == first.entity_count
    assert second.relation_count == first.relation_count
    assert second.definitions_matched == first.definitions_matched
    assert _document_count(graph) == 1

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

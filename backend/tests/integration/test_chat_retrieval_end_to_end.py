import os
import uuid
from pathlib import Path

import pytest

from jivaka.chat.service import ChatService
from jivaka.deid import Deidentifier
from jivaka.deid.backfill import deidentify_existing_patient_records
from jivaka.ingestion.linking.stub_dictionary import StubDictionarySource
from jivaka.ingestion.models import Chunk, Document, Entity
from jivaka.ingestion.graph_writer import GraphWriter
from jivaka.ingestion.pipeline import ingest_document
from jivaka.retrieval.graph_search import load_chunks, search

from .test_ingest_pipeline_end_to_end import FIXTURES_DIR, FakeEntityExtractor


@pytest.fixture
def graph():
    from falkordb import FalkorDB

    host = os.environ.get("FALKOR_HOST", "localhost")
    port = int(os.environ.get("FALKOR_PORT", "6379"))
    try:
        g = FalkorDB(host=host, port=port).select_graph(f"jivaka_test_{uuid.uuid4().hex[:8]}")
        g.query("RETURN 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"FalkorDB not reachable at {host}:{port} ({exc}). Run `docker compose up falkordb` first.")
    yield g
    try:
        g.delete()
    except Exception:  # noqa: BLE001
        pass


def ingest(graph, path: Path, doc_type: str):
    return ingest_document(
        path=path,
        doc_type=doc_type,
        graph=graph,
        extractor=FakeEntityExtractor(),
        definition_source=StubDictionarySource(),
        deidentifier=Deidentifier(),
    )


def test_patient_record_is_deidentified_and_searchable_by_pseudonym(graph, tmp_path):
    note = tmp_path / "somchai_note.txt"
    note.write_text(
        "Name: Somchai Jaidee\nHN: 6512345\n\n" + (FIXTURES_DIR / "sample_patient_note.txt").read_text()
    )
    result = ingest(graph, note, "patient_record")

    pseudonym = graph.query(
        "MATCH (d:Document {id: $id}) RETURN d.pseudonym", params={"id": result.document.id}
    ).result_set[0][0]
    assert pseudonym.startswith("P-")

    texts = [row[0] for row in graph.query(
        "MATCH (c:Chunk {doc_id: $id}) RETURN c.text", params={"id": result.document.id}
    ).result_set]
    assert texts and not any("Somchai" in t or "6512345" in t for t in texts)

    found = search(graph, ["pneumonia"], [pseudonym])
    assert found and found[0].source_label == f"Patient {pseudonym}"
    assert any(e.name == "pneumonia" for e in found[0].entities)
    assert "Somchai" not in found[0].source_label

    assert search(graph, ["pneumonia"], ["P-9999"]) == []
    assert search(graph, [], [pseudonym])  # pseudonym alone lists that patient's chunks


def test_search_ranks_across_documents_and_follows_relations(graph):
    ingest(graph, FIXTURES_DIR / "sample_textbook_excerpt.txt", "textbook")
    ingest(graph, FIXTURES_DIR / "sample_patient_note.txt", "patient_record")

    direct = search(graph, ["amoxicillin"], top_k=10)
    assert direct and all(c.score >= 1 for c in direct)

    # pneumonia is RELATED_TO amoxicillin, so asking about amoxicillin also
    # reaches chunks that only mention pneumonia, at a lower score.
    labels = {c.doc_type for c in search(graph, ["amoxicillin"], top_k=10)}
    assert "patient_record" in labels


def test_patient_chunks_that_are_not_deidentified_are_never_returned(graph):
    writer = GraphWriter(graph)
    doc = Document(filename="raw.txt", doc_type="patient_record", source_hash="h")
    writer.write_document(doc)
    chunk = Chunk(doc_id=doc.id, text="Mr. John Smith has fever.", order=0, source_type="patient_record")
    writer.write_chunk(chunk)
    writer.write_entity(chunk.id, Entity(
        doc_id=doc.id, chunk_id=chunk.id, name="fever", normalized_name="fever", entity_type="symptom"
    ))
    writer.write_entity(chunk.id, Entity(
        doc_id=doc.id, chunk_id=chunk.id, name="John Smith", normalized_name="john smith", entity_type="other"
    ))

    assert search(graph, ["fever"]) == []
    assert load_chunks(graph, [chunk.id]) == []

    results = deidentify_existing_patient_records(graph, Deidentifier())

    assert results[0].chunks_updated == 1 and results[0].entities_removed == 1
    found = search(graph, ["fever"])
    assert [c.text for c in found] == ["Mr. [PERSON-1] has fever."]
    assert found[0].source_label == f"Patient {results[0].pseudonym}"


class EchoLLM:
    def chat_stream(self, messages):
        yield "Use amoxicillin [1]."

    def generate_json(self, prompt):
        return {"terms": []}


def test_chat_service_end_to_end(graph):
    ingest(graph, FIXTURES_DIR / "sample_patient_note.txt", "patient_record")

    events = list(ChatService(graph, llm=EchoLLM()).answer(
        [{"role": "user", "content": "How is pneumonia treated?"}]
    ))

    sources = next(e.data for e in events if e.type == "sources")
    assert sources and sources[0]["source_label"].startswith("Patient P-")

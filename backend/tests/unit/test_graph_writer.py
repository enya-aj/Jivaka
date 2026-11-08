from jivaka.ingestion.graph_writer import GraphWriter
from jivaka.ingestion.models import ChunkExtraction, Definition, Document, Entity, EntityRelation


class FakeGraph:
    """Captures query calls instead of hitting a real FalkorDB instance."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def query(self, cypher, params=None):
        self.calls.append((cypher, params or {}))


def test_write_document_sends_expected_params():
    graph = FakeGraph()
    writer = GraphWriter(graph)
    document = Document(filename="a.txt", doc_type="textbook", source_hash="abc123")

    writer.write_document(document)

    assert len(graph.calls) == 1
    cypher, params = graph.calls[0]
    assert "MERGE (d:Document" in cypher
    assert params["id"] == document.id
    assert params["filename"] == "a.txt"
    assert params["source_hash"] == "abc123"


def test_write_chunk_extraction_writes_entities_definitions_and_relations():
    graph = FakeGraph()
    writer = GraphWriter(graph)

    fever = Entity(doc_id="d1", chunk_id="c1", name="fever", normalized_name="fever", entity_type="symptom")
    amox = Entity(
        doc_id="d1", chunk_id="c1", name="amoxicillin", normalized_name="amoxicillin", entity_type="treatment"
    )
    extraction = ChunkExtraction(
        chunk_id="c1",
        entities=[fever, amox],
        relations=[EntityRelation(source_entity_id=amox.id, target_entity_id=fever.id, relation_type="treats")],
    )

    definition = Definition(term="fever", definition_text="elevated body temp", source="stub")
    definitions_by_entity_id = {fever.id: definition, amox.id: None}

    entity_count, relation_count, matched, missing = writer.write_chunk_extraction(
        extraction, definitions_by_entity_id
    )

    assert entity_count == 2
    assert relation_count == 1
    assert matched == 1
    assert missing == 1

    # One batched query per kind, not one per item.
    assert len(graph.calls) == 3
    (entity_cypher, entity_params), (def_cypher, def_params), (rel_cypher, rel_params) = graph.calls
    assert "UNWIND $entities" in entity_cypher and "MERGE (e:Entity" in entity_cypher
    assert [row["name"] for row in entity_params["entities"]] == ["fever", "amoxicillin"]
    assert "MERGE (def:Definition" in def_cypher
    assert [(row["entity_id"], row["normalized_term"]) for row in def_params["rows"]] == [(fever.id, "fever")]
    assert "MERGE (a)-[:RELATED_TO" in rel_cypher
    assert rel_params["rows"] == [{"source_id": amox.id, "target_id": fever.id, "relation_type": "treats"}]


def test_write_extracted_chunk_marks_the_chunk_finished_last():
    from jivaka.ingestion.models import Chunk

    graph = FakeGraph()
    writer = GraphWriter(graph)
    chunk = Chunk(doc_id="d1", text="no entities here", order=0, source_type="textbook")

    counts = writer.write_extracted_chunk(chunk, ChunkExtraction(chunk_id=chunk.id), {})

    assert counts == (0, 0, 0, 0)
    assert len(graph.calls) == 2  # chunk (+ entities), then the flag
    assert "MERGE (c:Chunk" in graph.calls[0][0] and "c.extracted = false" in graph.calls[0][0]
    assert "SET c.extracted = true" in graph.calls[1][0]


def test_write_definition_merges_on_normalized_term_not_id():
    graph = FakeGraph()
    writer = GraphWriter(graph)
    definition = Definition(term="  Fever  ", definition_text="x", source="stub")

    writer.write_definition("entity-1", definition)

    _, params = graph.calls[0]
    assert params["normalized_term"] == "fever"
    assert "id" not in params

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

    cyphers = [c for c, _ in graph.calls]
    assert sum("MERGE (e:Entity" in c for c in cyphers) == 2
    assert sum("MERGE (def:Definition" in c for c in cyphers) == 1
    assert sum("MERGE (a)-[:RELATED_TO" in c for c in cyphers) == 1


def test_write_definition_merges_on_normalized_term_not_id():
    graph = FakeGraph()
    writer = GraphWriter(graph)
    definition = Definition(term="  Fever  ", definition_text="x", source="stub")

    writer.write_definition("entity-1", definition)

    _, params = graph.calls[0]
    assert params["normalized_term"] == "fever"
    assert "id" not in params

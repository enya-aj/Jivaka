from typing import Optional

from falkordb import Graph


def get_document_graph(graph: Graph, doc_id: str) -> Optional[dict]:
    """Read-only dump of a document's trinity subgraph, for CLI/API
    verification - not used by (future) retrieval, which will query FalkorDB
    directly."""
    doc_result = graph.query("MATCH (d:Document {id: $doc_id}) RETURN d", params={"doc_id": doc_id})
    if not doc_result.result_set:
        return None
    document = doc_result.result_set[0][0].properties

    chunks_result = graph.query(
        "MATCH (:Document {id: $doc_id})-[:HAS_CHUNK]->(c:Chunk) RETURN c ORDER BY c.order",
        params={"doc_id": doc_id},
    )
    chunks_by_id = {row[0].properties["id"]: row[0].properties for row in chunks_result.result_set}

    entities_result = graph.query(
        "MATCH (c:Chunk {doc_id: $doc_id})-[:MENTIONS]->(e:Entity) RETURN c.id, e",
        params={"doc_id": doc_id},
    )
    entities_by_chunk: dict[str, list[dict]] = {cid: [] for cid in chunks_by_id}
    entities_by_id: dict[str, dict] = {}
    for chunk_id, entity_node in entities_result.result_set:
        entity = entity_node.properties
        entities_by_id[entity["id"]] = entity
        entities_by_chunk.setdefault(chunk_id, []).append(entity)

    definitions_result = graph.query(
        "MATCH (e:Entity {doc_id: $doc_id})-[:DEFINED_AS]->(def:Definition) RETURN e.id, def",
        params={"doc_id": doc_id},
    )
    definition_by_entity_id = {row[0]: row[1].properties for row in definitions_result.result_set}

    relations_result = graph.query(
        "MATCH (a:Entity {doc_id: $doc_id})-[r:RELATED_TO]->(b:Entity) "
        "RETURN a.id, a.name, r.relation_type, b.id, b.name",
        params={"doc_id": doc_id},
    )
    relations = [
        {
            "source_entity_id": row[0],
            "source_name": row[1],
            "relation_type": row[2],
            "target_entity_id": row[3],
            "target_name": row[4],
        }
        for row in relations_result.result_set
    ]

    chunks = []
    for chunk_id, chunk in sorted(chunks_by_id.items(), key=lambda kv: kv[1].get("order", 0)):
        chunk_entities = []
        for entity in entities_by_chunk.get(chunk_id, []):
            chunk_entities.append(
                {
                    "entity": entity,
                    "definition": definition_by_entity_id.get(entity["id"]),
                }
            )
        chunks.append({"chunk": chunk, "entities": chunk_entities})

    return {
        "document": document,
        "chunks": chunks,
        "relations": relations,
    }

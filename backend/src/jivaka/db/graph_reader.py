from typing import Optional

from falkordb import Graph


def list_documents(graph: Graph, limit: int = 50) -> list[dict]:
    """Most-recently-ingested documents first, for a simple browsing list -
    not a substitute for the (future) retrieval layer."""
    result = graph.query(
        "MATCH (d:Document) RETURN d ORDER BY d.ingested_at DESC LIMIT $limit",
        params={"limit": limit},
    )
    return [row[0].properties for row in result.result_set]


def find_documents_by_hash(graph: Graph, source_hash: str, doc_type: str) -> list[dict]:
    """Documents already ingested from identical file content as the same
    doc_type, used to skip re-ingesting a duplicate upload. Each dict
    carries `ingestion_complete` (true, false while running/after a crash,
    or missing on documents written before that flag existed)."""
    result = graph.query(
        "MATCH (d:Document {source_hash: $source_hash, doc_type: $doc_type}) "
        "RETURN d ORDER BY d.ingested_at",
        params={"source_hash": source_hash, "doc_type": doc_type},
    )
    return [row[0].properties for row in result.result_set]


def get_document_graph(graph: Graph, doc_id: str, max_chunks: Optional[int] = None) -> Optional[dict]:
    """Read-only dump of a document's trinity subgraph, for CLI/API
    verification - not used by (future) retrieval, which will query FalkorDB
    directly.

    With `max_chunks`, only the first that many chunks (by order) are read,
    with their entities, definitions and the relations among those entities.
    Without it a very large document (tens of thousands of chunks) makes
    these queries time out. `total_chunks` and `truncated` in the result say
    how much was left out."""
    doc_result = graph.query("MATCH (d:Document {id: $doc_id}) RETURN d", params={"doc_id": doc_id})
    if not doc_result.result_set:
        return None
    document = doc_result.result_set[0][0].properties

    if max_chunks is None:
        return _full_document_graph(graph, doc_id, document)

    total_result = graph.query(
        "MATCH (:Document {id: $doc_id})-[:HAS_CHUNK]->(c:Chunk) RETURN count(c)",
        params={"doc_id": doc_id},
    )
    total_chunks = total_result.result_set[0][0] if total_result.result_set else 0

    chunks_result = graph.query(
        "MATCH (:Document {id: $doc_id})-[:HAS_CHUNK]->(c:Chunk) RETURN c ORDER BY c.order LIMIT $limit",
        params={"doc_id": doc_id, "limit": max_chunks},
    )
    chunks_by_id = {row[0].properties["id"]: row[0].properties for row in chunks_result.result_set}
    chunk_ids = list(chunks_by_id)

    entities_result = graph.query(
        "MATCH (c:Chunk)-[:MENTIONS]->(e:Entity) WHERE c.id IN $chunk_ids RETURN c.id, e",
        params={"chunk_ids": chunk_ids},
    )
    entities_by_chunk, entities_by_id = _group_entities(chunks_by_id, entities_result.result_set)
    entity_ids = list(entities_by_id)

    definitions_result = graph.query(
        "MATCH (e:Entity)-[:DEFINED_AS]->(def:Definition) WHERE e.id IN $entity_ids RETURN e.id, def",
        params={"entity_ids": entity_ids},
    )
    relations_result = graph.query(
        "MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity) WHERE a.id IN $entity_ids AND b.id IN $entity_ids "
        "RETURN a.id, a.name, r.relation_type, b.id, b.name",
        params={"entity_ids": entity_ids},
    )
    dump = _assemble(document, chunks_by_id, entities_by_chunk, definitions_result.result_set, relations_result.result_set)
    dump["total_chunks"] = total_chunks
    dump["truncated"] = total_chunks > len(chunks_by_id)
    return dump


def _group_entities(chunks_by_id: dict, rows) -> tuple[dict, dict]:
    entities_by_chunk: dict[str, list[dict]] = {cid: [] for cid in chunks_by_id}
    entities_by_id: dict[str, dict] = {}
    for chunk_id, entity_node in rows:
        entity = entity_node.properties
        entities_by_id[entity["id"]] = entity
        entities_by_chunk.setdefault(chunk_id, []).append(entity)
    return entities_by_chunk, entities_by_id


def _full_document_graph(graph: Graph, doc_id: str, document: dict) -> dict:
    chunks_result = graph.query(
        "MATCH (:Document {id: $doc_id})-[:HAS_CHUNK]->(c:Chunk) RETURN c ORDER BY c.order",
        params={"doc_id": doc_id},
    )
    chunks_by_id = {row[0].properties["id"]: row[0].properties for row in chunks_result.result_set}

    entities_result = graph.query(
        "MATCH (c:Chunk {doc_id: $doc_id})-[:MENTIONS]->(e:Entity) RETURN c.id, e",
        params={"doc_id": doc_id},
    )
    entities_by_chunk, _ = _group_entities(chunks_by_id, entities_result.result_set)

    definitions_result = graph.query(
        "MATCH (e:Entity {doc_id: $doc_id})-[:DEFINED_AS]->(def:Definition) RETURN e.id, def",
        params={"doc_id": doc_id},
    )
    relations_result = graph.query(
        "MATCH (a:Entity {doc_id: $doc_id})-[r:RELATED_TO]->(b:Entity) "
        "RETURN a.id, a.name, r.relation_type, b.id, b.name",
        params={"doc_id": doc_id},
    )
    dump = _assemble(document, chunks_by_id, entities_by_chunk, definitions_result.result_set, relations_result.result_set)
    dump["total_chunks"] = len(chunks_by_id)
    dump["truncated"] = False
    return dump


def _assemble(document: dict, chunks_by_id: dict, entities_by_chunk: dict, definition_rows, relation_rows) -> dict:
    definition_by_entity_id = {row[0]: row[1].properties for row in definition_rows}
    relations = [
        {
            "source_entity_id": row[0],
            "source_name": row[1],
            "relation_type": row[2],
            "target_entity_id": row[3],
            "target_name": row[4],
        }
        for row in relation_rows
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

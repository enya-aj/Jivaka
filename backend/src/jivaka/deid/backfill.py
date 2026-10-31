from dataclasses import dataclass

from falkordb import Graph

from jivaka.deid.deidentifier import Deidentifier
from jivaka.ingestion.graph_writer import GraphWriter


@dataclass
class BackfillResult:
    doc_id: str
    pseudonym: str
    chunks_updated: int
    entities_removed: int


def deidentify_existing_patient_records(graph: Graph, deidentifier: Deidentifier) -> list[BackfillResult]:
    """De-identifies patient records ingested before jivaka.deid existed, in
    place: chunk text is replaced with its de-identified version, entities
    whose names contained an identifier are deleted, and each document gets a
    pseudonym. Already de-identified documents are left as they are (apart
    from getting a pseudonym if they lack one)."""
    writer = GraphWriter(graph)
    docs = graph.query("MATCH (d:Document {doc_type: 'patient_record'}) RETURN d.id ORDER BY d.ingested_at")
    results = []
    for (doc_id,) in docs.result_set:
        chunks = graph.query(
            "MATCH (c:Chunk {doc_id: $doc_id}) RETURN c.id, c.text, c.deidentified ORDER BY c.order",
            params={"doc_id": doc_id},
        ).result_set
        pending = [(cid, text) for cid, text, done in chunks if not done]
        chunks_updated = 0
        entities_removed = 0
        if pending:
            entities = graph.query(
                "MATCH (e:Entity {doc_id: $doc_id}) RETURN e.id, e.name", params={"doc_id": doc_id}
            ).result_set
            # One call over chunks and entity names together, so a name seen
            # in any chunk is also recognised inside an entity name.
            texts = [text or "" for _, text in pending] + [name or "" for _, name in entities]
            cleaned = deidentifier.deidentify_texts(texts)
            for (chunk_id, _), new_text in zip(pending, cleaned):
                graph.query(
                    "MATCH (c:Chunk {id: $id}) SET c.text = $text, c.deidentified = true",
                    params={"id": chunk_id, "text": new_text},
                )
                chunks_updated += 1
            for (entity_id, name), new_name in zip(entities, cleaned[len(pending) :]):
                if new_name != (name or ""):
                    graph.query("MATCH (e:Entity {id: $id}) DETACH DELETE e", params={"id": entity_id})
                    entities_removed += 1
        pseudonym = writer.assign_pseudonym(doc_id)
        results.append(BackfillResult(doc_id, pseudonym, chunks_updated, entities_removed))
    return results

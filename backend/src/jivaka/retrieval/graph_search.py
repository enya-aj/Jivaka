import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, Sequence

from falkordb import Graph

# Weight of a chunk mentioning an entity one RELATED_TO hop away from one
# named in the question, relative to a direct mention.
RELATED_WEIGHT = 0.5

# Patient chunks are only ever read once de-identified (see jivaka.deid).
_READABLE = "(c.source_type <> 'patient_record' OR c.deidentified = true)"

# Uploads are saved as "<uuid hex>_<original name>" (see api/routes.py).
_UPLOAD_PREFIX_RE = re.compile(r"^[0-9a-f]{32}_")


@dataclass
class SourceEntity:
    name: str
    entity_type: str
    definition: Optional[dict] = None


@dataclass
class RetrievedChunk:
    chunk_id: str
    doc_id: str
    doc_type: str
    source_label: str
    text: str
    order: int
    page_number: Optional[int]
    score: float = 0.0
    entities: list[SourceEntity] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "doc_type": self.doc_type,
            "source_label": self.source_label,
            "text": self.text,
            "page_number": self.page_number,
            "score": self.score,
            "entities": [
                {"name": e.name, "entity_type": e.entity_type, "definition": e.definition} for e in self.entities
            ],
        }


def search(
    graph: Graph, terms: Sequence[str], pseudonyms: Sequence[str] = (), top_k: int = 6
) -> list[RetrievedChunk]:
    """Ranks chunks across the whole graph by how many of the question's
    entities they mention (directly, via a shared Definition, or one
    RELATED_TO hop away). A patient pseudonym in the question narrows the
    search to that patient's records."""
    doc_ids: Optional[list[str]] = None
    if pseudonyms:
        rows = graph.query(
            "MATCH (d:Document) WHERE d.pseudonym IN $pseudonyms RETURN d.id", params={"pseudonyms": list(pseudonyms)}
        ).result_set
        doc_ids = [row[0] for row in rows]
        if not doc_ids:
            return []

    scores: dict[str, float] = defaultdict(float)
    if terms:
        direct = set(terms)
        rows = graph.query(
            "MATCH (e:Entity)-[:DEFINED_AS]->(def:Definition) WHERE def.normalized_term IN $terms "
            "RETURN DISTINCT e.normalized_name",
            params={"terms": list(terms)},
        ).result_set
        direct.update(row[0] for row in rows)

        rows = graph.query(
            "MATCH (e:Entity)-[:RELATED_TO]-(r:Entity) WHERE e.normalized_name IN $names "
            "RETURN DISTINCT r.normalized_name",
            params={"names": sorted(direct)},
        ).result_set
        related = {row[0] for row in rows} - direct

        for names, weight in ((direct, 1.0), (related, RELATED_WEIGHT)):
            if not names:
                continue
            for chunk_id, hits in _mention_counts(graph, names, doc_ids):
                scores[chunk_id] += hits * weight
    elif doc_ids:
        rows = graph.query(
            f"MATCH (c:Chunk) WHERE c.doc_id IN $doc_ids AND {_READABLE} "
            "RETURN c.id ORDER BY c.order LIMIT $limit",
            params={"doc_ids": doc_ids, "limit": top_k},
        ).result_set
        scores.update({row[0]: 0.0 for row in rows})

    if not scores:
        return []
    chunks = {c.chunk_id: c for c in load_chunks(graph, list(scores))}
    ranked = sorted(chunks.values(), key=lambda c: (-scores[c.chunk_id], c.doc_id, c.order))[:top_k]
    for chunk in ranked:
        chunk.score = scores[chunk.chunk_id]
    return ranked


def _mention_counts(graph: Graph, names: set[str], doc_ids: Optional[list[str]]) -> list[tuple[str, int]]:
    doc_filter = "AND c.doc_id IN $doc_ids " if doc_ids is not None else ""
    rows = graph.query(
        f"MATCH (c:Chunk)-[:MENTIONS]->(e:Entity) WHERE e.normalized_name IN $names AND {_READABLE} "
        f"{doc_filter}RETURN c.id, count(DISTINCT e.normalized_name)",
        params={"names": sorted(names), "doc_ids": doc_ids or []},
    ).result_set
    return [(row[0], row[1]) for row in rows]


def load_chunks(graph: Graph, chunk_ids: Sequence[str]) -> list[RetrievedChunk]:
    """Chunks with their document label, entities and definitions. Patient
    chunks that haven't been de-identified are never returned."""
    rows = graph.query(
        f"MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) WHERE c.id IN $ids AND {_READABLE} "
        "RETURN c, d.doc_type, d.filename, d.pseudonym",
        params={"ids": list(chunk_ids)},
    ).result_set
    chunks: dict[str, RetrievedChunk] = {}
    for node, doc_type, filename, pseudonym in rows:
        props = node.properties
        if doc_type == "patient_record":
            # Never the filename: it often carries the patient's name.
            label = f"Patient {pseudonym}" if pseudonym else "Patient record"
        else:
            label = _UPLOAD_PREFIX_RE.sub("", filename or "") or "Unknown document"
        chunks[props["id"]] = RetrievedChunk(
            chunk_id=props["id"],
            doc_id=props.get("doc_id", ""),
            doc_type=doc_type or props.get("source_type", ""),
            source_label=label,
            text=props.get("text", ""),
            order=props.get("order", 0),
            page_number=props.get("page_number"),
        )
    if not chunks:
        return []

    rows = graph.query(
        "MATCH (c:Chunk)-[:MENTIONS]->(e:Entity) WHERE c.id IN $ids "
        "OPTIONAL MATCH (e)-[:DEFINED_AS]->(def:Definition) "
        "RETURN c.id, e.name, e.entity_type, def.term, def.definition_text, def.source",
        params={"ids": list(chunks)},
    ).result_set
    for chunk_id, name, entity_type, term, definition_text, source in rows:
        definition = (
            {"term": term, "definition_text": definition_text, "source": source} if definition_text else None
        )
        chunks[chunk_id].entities.append(SourceEntity(name=name, entity_type=entity_type, definition=definition))
    return list(chunks.values())

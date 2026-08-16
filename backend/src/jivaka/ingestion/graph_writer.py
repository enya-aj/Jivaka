from typing import Optional

from falkordb import Graph

from jivaka.ingestion.models import Chunk, ChunkExtraction, Definition, Document
from jivaka.ingestion.text_utils import normalize_term


class GraphWriter:
    """Persists a document's trinity subgraph into the shared FalkorDB graph.
    Every node is tagged with doc_id (except Definition, which is
    intentionally shared/deduplicated across documents via MERGE)."""

    def __init__(self, graph: Graph):
        self.graph = graph

    def write_document(self, document: Document) -> None:
        self.graph.query(
            """
            MERGE (d:Document {id: $id})
            SET d.filename = $filename,
                d.doc_type = $doc_type,
                d.source_hash = $source_hash,
                d.ocr_used = $ocr_used,
                d.ingested_at = $ingested_at
            """,
            params={
                "id": document.id,
                "filename": document.filename,
                "doc_type": document.doc_type,
                "source_hash": document.source_hash,
                "ocr_used": document.ocr_used,
                "ingested_at": document.ingested_at.isoformat(),
            },
        )

    def write_chunk(self, chunk: Chunk) -> None:
        self.graph.query(
            """
            MATCH (d:Document {id: $doc_id})
            MERGE (c:Chunk {id: $id})
            SET c.doc_id = $doc_id,
                c.text = $text,
                c.order = $order,
                c.source_type = $source_type,
                c.page_number = $page_number,
                c.created_at = $created_at
            MERGE (d)-[:HAS_CHUNK]->(c)
            """,
            params={
                "id": chunk.id,
                "doc_id": chunk.doc_id,
                "text": chunk.text,
                "order": chunk.order,
                "source_type": chunk.source_type,
                "page_number": chunk.page_number,
                "created_at": chunk.created_at.isoformat(),
            },
        )

    def write_entity(self, chunk_id: str, entity) -> None:
        self.graph.query(
            """
            MATCH (c:Chunk {id: $chunk_id})
            MERGE (e:Entity {id: $id})
            SET e.doc_id = $doc_id,
                e.name = $name,
                e.normalized_name = $normalized_name,
                e.entity_type = $entity_type,
                e.extraction_source = $extraction_source,
                e.confidence = $confidence
            MERGE (c)-[:MENTIONS]->(e)
            """,
            params={
                "chunk_id": chunk_id,
                "id": entity.id,
                "doc_id": entity.doc_id,
                "name": entity.name,
                "normalized_name": entity.normalized_name,
                "entity_type": entity.entity_type,
                "extraction_source": entity.extraction_source,
                "confidence": entity.confidence,
            },
        )

    def write_relation(self, source_entity_id: str, target_entity_id: str, relation_type: str) -> None:
        self.graph.query(
            """
            MATCH (a:Entity {id: $source_id}), (b:Entity {id: $target_id})
            MERGE (a)-[:RELATED_TO {relation_type: $relation_type}]->(b)
            """,
            params={
                "source_id": source_entity_id,
                "target_id": target_entity_id,
                "relation_type": relation_type,
            },
        )

    def write_definition(self, entity_id: str, definition: Definition) -> None:
        # Definitions are intentionally deduplicated/shared across documents:
        # MERGE on normalized term + source, not on definition.id.
        self.graph.query(
            """
            MATCH (e:Entity {id: $entity_id})
            MERGE (def:Definition {normalized_term: $normalized_term, source: $source})
            SET def.term = $term,
                def.definition_text = $definition_text,
                def.source_id = $source_id,
                def.semantic_type = $semantic_type
            MERGE (e)-[:DEFINED_AS]->(def)
            """,
            params={
                "entity_id": entity_id,
                "normalized_term": normalize_term(definition.term),
                "source": definition.source,
                "term": definition.term,
                "definition_text": definition.definition_text,
                "source_id": definition.source_id,
                "semantic_type": definition.semantic_type,
            },
        )

    def write_chunk_extraction(
        self, extraction: ChunkExtraction, definitions_by_entity_id: dict[str, Optional[Definition]]
    ) -> tuple[int, int, int, int]:
        """Writes all entities/relations/definitions for one chunk's
        extraction. Returns (entity_count, relation_count, definitions_matched,
        definitions_missing)."""
        definitions_matched = 0
        definitions_missing = 0

        for entity in extraction.entities:
            self.write_entity(extraction.chunk_id, entity)
            definition = definitions_by_entity_id.get(entity.id)
            if definition is not None:
                self.write_definition(entity.id, definition)
                definitions_matched += 1
            else:
                definitions_missing += 1

        for relation in extraction.relations:
            self.write_relation(
                relation.source_entity_id, relation.target_entity_id, relation.relation_type
            )

        return len(extraction.entities), len(extraction.relations), definitions_matched, definitions_missing

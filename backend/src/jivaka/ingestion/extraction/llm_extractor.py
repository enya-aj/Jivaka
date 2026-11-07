import logging
import time
from typing import Optional, get_args

from jivaka.config import get_settings
from jivaka.ingestion.extraction.base import EntityExtractor
from jivaka.ingestion.extraction.llm_client import OllamaClient
from jivaka.ingestion.models import Chunk, ChunkExtraction, Entity, EntityRelation, EntityType
from jivaka.ingestion.text_utils import normalize_term

logger = logging.getLogger(__name__)

_VALID_ENTITY_TYPES = set(get_args(EntityType))

_PROMPT_TEMPLATE = """You are a clinical information extraction assistant. Given a passage of \
clinical or medical text, extract:

1. entities: symptoms, diagnoses, and treatments (drugs or procedures) explicitly mentioned, \
each with its surface-form name and entity_type (one of: symptom, diagnosis, treatment, other).
2. relations: any explicit relationship between two of the extracted entities \
(e.g. "treats", "causes", "indicates"), referencing entities by their exact name text.

Respond with strict JSON only, matching this shape, and no other text:
{{"entities": [{{"name": "...", "entity_type": "symptom|diagnosis|treatment|other"}}], \
"relations": [{{"source": "...", "target": "...", "relation_type": "..."}}]}}

Text:
\"\"\"
{text}
\"\"\"
"""


class LLMEntityExtractor(EntityExtractor):
    """Safe to call from several threads at once (the pipeline runs
    extractions concurrently); the underlying httpx client is thread-safe."""

    def __init__(self, client: Optional[OllamaClient] = None, retries: Optional[int] = None):
        self.client = client or OllamaClient()
        self.retries = get_settings().ingest_extraction_retries if retries is None else retries

    def extract(self, chunk: Chunk) -> ChunkExtraction:
        prompt = _PROMPT_TEMPLATE.format(text=chunk.text)
        raw = None
        for attempt in range(self.retries + 1):
            try:
                raw = self.client.generate_json(prompt)
                break
            except Exception:
                if attempt < self.retries:
                    logger.warning("LLM extraction failed for chunk %s, retrying", chunk.id, exc_info=True)
                    time.sleep(2**attempt)
                else:
                    logger.exception("LLM extraction failed for chunk %s", chunk.id)
        if not isinstance(raw, dict):
            return ChunkExtraction(chunk_id=chunk.id, failed=True)

        entities, by_normalized_name = self._parse_entities(chunk, raw.get("entities", []))
        relations = self._parse_relations(raw.get("relations", []), by_normalized_name)
        return ChunkExtraction(chunk_id=chunk.id, entities=entities, relations=relations)

    def _parse_entities(
        self, chunk: Chunk, raw_entities: list
    ) -> tuple[list[Entity], dict[str, Entity]]:
        entities: list[Entity] = []
        by_normalized_name: dict[str, Entity] = {}
        for item in raw_entities:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            entity_type = item.get("entity_type", "other")
            if entity_type not in _VALID_ENTITY_TYPES:
                entity_type = "other"
            entity = Entity(
                doc_id=chunk.doc_id,
                chunk_id=chunk.id,
                name=name,
                normalized_name=normalize_term(name),
                entity_type=entity_type,
                extraction_source="llm",
            )
            entities.append(entity)
            by_normalized_name[normalize_term(name)] = entity
        return entities, by_normalized_name

    def _parse_relations(
        self, raw_relations: list, by_normalized_name: dict[str, Entity]
    ) -> list[EntityRelation]:
        relations: list[EntityRelation] = []
        for item in raw_relations:
            if not isinstance(item, dict):
                continue
            source = by_normalized_name.get(normalize_term(str(item.get("source", ""))))
            target = by_normalized_name.get(normalize_term(str(item.get("target", ""))))
            if not source or not target or source.id == target.id:
                continue
            relation_type = str(item.get("relation_type") or "related_to").strip() or "related_to"
            relations.append(
                EntityRelation(
                    source_entity_id=source.id,
                    target_entity_id=target.id,
                    relation_type=relation_type,
                )
            )
        return relations

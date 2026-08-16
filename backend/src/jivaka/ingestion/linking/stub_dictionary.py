import json
from pathlib import Path
from typing import Optional

from jivaka.ingestion.linking.base import DefinitionSource
from jivaka.ingestion.models import Definition
from jivaka.ingestion.text_utils import normalize_term

# backend/src/jivaka/ingestion/linking/stub_dictionary.py -> repo root, matching
# both the local checkout layout and the /app/{backend,data} layout in Docker
# (see the `backend` service volumes in docker-compose.yml).
_DEFAULT_PATH = Path(__file__).resolve().parents[5] / "data" / "reference" / "definitions_stub.json"


class StubDictionarySource(DefinitionSource):
    """Small curated offline dataset. Explicitly NOT a real MeSH/UMLS
    extract - a placeholder until a licensed data source is wired up via
    umls_source.py."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or _DEFAULT_PATH
        self._by_term = self._load()

    def _load(self) -> dict[str, dict]:
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        return {normalize_term(entry["term"]): entry for entry in data.get("terms", [])}

    def lookup(self, term: str, entity_type: str) -> Optional[Definition]:
        entry = self._by_term.get(normalize_term(term))
        if entry is None:
            return None
        return Definition(
            term=entry["term"],
            definition_text=entry["definition_text"],
            source=entry.get("source", "stub"),
            source_id=entry.get("source_id"),
            semantic_type=entry.get("semantic_type"),
        )

from pathlib import Path

import yaml

from jivaka_corpus_fetch.models import SourceEntry

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "registry" / "sources.yaml"


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> list[SourceEntry]:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a top-level YAML list of source entries")

    entries: list[SourceEntry] = []
    seen_names: set[str] = set()
    for item in raw:
        entry = SourceEntry(**item)
        if entry.name in seen_names:
            raise ValueError(f"Duplicate registry entry name: {entry.name!r}")
        seen_names.add(entry.name)
        entries.append(entry)
    return entries


def filter_entries(
    entries: list[SourceEntry],
    category: str | None = None,
    license_class: str | None = None,
) -> list[SourceEntry]:
    result = entries
    if category:
        result = [e for e in result if e.category == category]
    if license_class:
        result = [e for e in result if e.license_class == license_class]
    return result

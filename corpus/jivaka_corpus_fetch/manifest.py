from pathlib import Path

from jivaka_corpus_fetch.models import MANIFEST_COLUMNS, ManifestRow

DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "manifest.tsv"

_HEADER = "\t".join(MANIFEST_COLUMNS)


def read_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> dict[str, ManifestRow]:
    """Returns rows keyed by `name` - the lookup used for idempotent re-runs."""
    if not path.exists():
        return {}

    rows: dict[str, ManifestRow] = {}
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    for line in lines[1:]:  # skip header
        if not line.strip():
            continue
        row = ManifestRow.from_tsv_row(line)
        rows[row.name] = row
    return rows


def write_manifest(rows: dict[str, ManifestRow], path: Path = DEFAULT_MANIFEST_PATH) -> None:
    """Rewrites the whole manifest from the given rows, keyed by name so a
    re-run updates a row in place rather than duplicating it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_HEADER + "\n")
        for row in rows.values():
            f.write(row.to_tsv_row() + "\n")


def upsert(rows: dict[str, ManifestRow], row: ManifestRow) -> dict[str, ManifestRow]:
    rows[row.name] = row
    return rows

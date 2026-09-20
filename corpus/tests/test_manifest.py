from pathlib import Path

from jivaka_corpus_fetch.manifest import read_manifest, write_manifest
from jivaka_corpus_fetch.models import ManifestRow


def _row(name: str, status: str = "OK", **kwargs) -> ManifestRow:
    defaults = dict(
        status=status,
        bytes=100,
        sha256_12="abc123def456",
        path=f"green/vocabulary/{name}/raw/{name}.xml",
        url=f"https://example.org/{name}.xml",
        category="vocabulary",
        license_class="green",
        name=name,
    )
    defaults.update(kwargs)
    return ManifestRow(**defaults)


def test_write_then_read_round_trips(tmp_path: Path):
    manifest_path = tmp_path / "manifest.tsv"
    rows = {"mesh-desc": _row("mesh-desc")}

    write_manifest(rows, path=manifest_path)
    read_back = read_manifest(path=manifest_path)

    assert "mesh-desc" in read_back
    assert read_back["mesh-desc"].status == "OK"
    assert read_back["mesh-desc"].bytes == 100


def test_updating_existing_row_does_not_duplicate(tmp_path: Path):
    manifest_path = tmp_path / "manifest.tsv"
    rows = {"mesh-desc": _row("mesh-desc", status="FAIL")}
    write_manifest(rows, path=manifest_path)

    # second run: same name, now succeeds
    rows2 = read_manifest(path=manifest_path)
    rows2["mesh-desc"] = _row("mesh-desc", status="OK")
    write_manifest(rows2, path=manifest_path)

    final = read_manifest(path=manifest_path)
    assert len(final) == 1
    assert final["mesh-desc"].status == "OK"


def test_read_missing_manifest_returns_empty(tmp_path: Path):
    assert read_manifest(path=tmp_path / "does-not-exist.tsv") == {}


def test_skip_statuses_distinguishable_from_ok(tmp_path: Path):
    manifest_path = tmp_path / "manifest.tsv"
    rows = {
        "a": _row("a", status="OK"),
        "b": _row("b", status="SKIP-GATED", notes="needs UTS account"),
        "c": _row("c", status="SKIP-BLOCKED", notes="Cloudflare"),
        "d": _row("d", status="FAIL-HTML", notes="soft-404"),
    }
    write_manifest(rows, path=manifest_path)

    read_back = read_manifest(path=manifest_path)
    assert read_back["a"].status == "OK"
    assert read_back["b"].status == "SKIP-GATED"
    assert read_back["c"].status == "SKIP-BLOCKED"
    assert read_back["d"].status == "FAIL-HTML"

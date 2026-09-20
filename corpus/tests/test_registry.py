from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from jivaka_corpus_fetch.registry import DEFAULT_REGISTRY_PATH, filter_entries, load_registry


def test_real_registry_loads_without_error():
    entries = load_registry()
    assert len(entries) > 50  # sanity: this should be a substantial, populated registry


def test_real_registry_has_no_duplicate_names():
    entries = load_registry()
    names = [e.name for e in entries]
    assert len(names) == len(set(names))


def test_fetchable_entries_all_have_dest():
    for entry in load_registry():
        if entry.fetchable:
            assert entry.dest, f"{entry.name}: fetchable=true but no dest"


def test_unfetchable_entries_all_have_skip_reason():
    for entry in load_registry():
        if not entry.fetchable:
            assert entry.skip_reason, f"{entry.name}: fetchable=false but no skip_reason"


def test_filter_by_category():
    entries = load_registry()
    vocab_only = filter_entries(entries, category="vocabulary")
    assert vocab_only
    assert all(e.category == "vocabulary" for e in vocab_only)


def test_filter_by_license_class():
    entries = load_registry()
    green_only = filter_entries(entries, license_class="green")
    assert green_only
    assert all(e.license_class == "green" for e in green_only)


def test_filter_combines_both():
    entries = load_registry()
    filtered = filter_entries(entries, category="corpus", license_class="amber")
    assert all(e.category == "corpus" and e.license_class == "amber" for e in filtered)


def test_malformed_entry_missing_required_field_raises(tmp_path: Path):
    bad_registry = tmp_path / "bad.yaml"
    bad_registry.write_text(
        yaml.dump([{"name": "incomplete-entry", "category": "vocabulary"}]), encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        load_registry(path=bad_registry)


def test_bad_license_class_enum_raises(tmp_path: Path):
    bad_registry = tmp_path / "bad.yaml"
    bad_registry.write_text(
        yaml.dump(
            [
                {
                    "name": "bad-license",
                    "category": "vocabulary",
                    "license_class": "purple",  # not a valid enum value
                    "url": "https://example.org/x",
                    "dest": "green/vocabulary/x/raw/x.xml",
                    "fetchable": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_registry(path=bad_registry)


def test_fetchable_without_dest_raises(tmp_path: Path):
    bad_registry = tmp_path / "bad.yaml"
    bad_registry.write_text(
        yaml.dump(
            [
                {
                    "name": "no-dest",
                    "category": "vocabulary",
                    "license_class": "green",
                    "url": "https://example.org/x",
                    "fetchable": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_registry(path=bad_registry)


def test_unfetchable_without_skip_reason_raises(tmp_path: Path):
    bad_registry = tmp_path / "bad.yaml"
    bad_registry.write_text(
        yaml.dump(
            [
                {
                    "name": "no-reason",
                    "category": "vocabulary",
                    "license_class": "amber",
                    "url": "https://example.org/x",
                    "fetchable": False,
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_registry(path=bad_registry)


def test_default_registry_path_exists():
    assert DEFAULT_REGISTRY_PATH.exists()

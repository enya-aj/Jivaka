from pathlib import Path

import httpx
import pytest

from jivaka_corpus_fetch import downloader
from jivaka_corpus_fetch.models import ManifestRow, SourceEntry


@pytest.fixture(autouse=True)
def isolated_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(downloader, "ARCHIVE_ROOT", tmp_path)
    yield tmp_path


def _entry(**overrides) -> SourceEntry:
    defaults = dict(
        name="test-source",
        category="vocabulary",
        license_class="green",
        url="https://example.org/file.xml",
        dest="green/vocabulary/test/raw/file.xml",
        expected_content_type=["application/xml"],
        expected_extension=".xml",
        min_bytes=10,
        fetchable=True,
    )
    defaults.update(overrides)
    return SourceEntry(**defaults)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_one_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<?xml version='1.0'?><root>ok</root>", headers={"content-type": "application/xml"})

    row = downloader.fetch_one(_entry(), _client(handler))

    assert row.status == "OK"
    assert row.bytes > 0
    assert (downloader.ARCHIVE_ROOT / "green/vocabulary/test/raw/file.xml").exists()


def test_fetch_one_soft_404_caught():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html><body>404 not found</body></html>", headers={"content-type": "text/xml"}
        )

    row = downloader.fetch_one(_entry(min_bytes=1), _client(handler))

    assert row.status == "FAIL-HTML"
    assert not (downloader.ARCHIVE_ROOT / "green/vocabulary/test/raw/file.xml").exists()


def test_fetch_one_retries_then_succeeds():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 2:
            return httpx.Response(500)
        return httpx.Response(200, content=b"<?xml version='1.0'?><root>ok</root>")

    row = downloader.fetch_one(_entry(), _client(handler))

    assert row.status == "OK"
    assert attempts["count"] == 2


def test_fetch_one_exhausts_retries_and_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    row = downloader.fetch_one(_entry(), _client(handler))

    assert row.status == "FAIL"


def test_skip_row_never_touches_network():
    entry = _entry(fetchable=False, dest=None, skip_reason="registration required: UTS account")
    row = downloader.skip_row(entry)
    assert row.status == "SKIP-GATED"

    entry2 = _entry(fetchable=False, dest=None, skip_reason="confirmed Cloudflare-blocked")
    assert downloader.skip_row(entry2).status == "SKIP-BLOCKED"

    entry3 = _entry(fetchable=False, dest=None, skip_reason="confirmed dead (404)")
    assert downloader.skip_row(entry3).status == "SKIP-DEAD"


def test_already_verified_skips_rehash_mismatch_triggers_refetch(tmp_path: Path):
    entry = _entry()
    dest_path = downloader.ARCHIVE_ROOT / entry.dest
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(b"some content")
    real_hash = downloader.sha256_of_file(dest_path)

    good_row = ManifestRow(
        status="OK",
        bytes=12,
        sha256_12=real_hash,
        path=entry.dest,
        url=entry.url,
        category=entry.category,
        license_class=entry.license_class,
        name=entry.name,
    )
    assert downloader.already_verified(entry, good_row) is True

    stale_row = good_row.model_copy(update={"sha256_12": "0000deadbeef"})
    assert downloader.already_verified(entry, stale_row) is False

    assert downloader.already_verified(entry, None) is False

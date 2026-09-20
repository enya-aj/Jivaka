from pathlib import Path

import pytest

from jivaka_corpus_fetch.verify import VerificationError, sniff_content_kind, verify_content

FIXTURES = Path(__file__).parent / "fixtures"


def test_valid_xml_passes():
    body = (FIXTURES / "sample_valid_small.xml").read_bytes()
    kind = verify_content(
        body_head=body, total_bytes=len(body), expected_extension=".xml", min_bytes=10
    )
    assert kind == "xml"


def test_soft_404_html_body_fails_even_with_200_status():
    """Direct regression test for the documented MeSH asciimesh soft-404
    bug: HTTP 200 status but an HTML error page instead of the real file."""
    body = (FIXTURES / "sample_html_error_body.html").read_bytes()
    with pytest.raises(VerificationError) as exc_info:
        verify_content(
            body_head=body, total_bytes=len(body), expected_extension=".xml", min_bytes=10
        )
    assert exc_info.value.status == "FAIL-HTML"


def test_html_expected_html_passes():
    body = (FIXTURES / "sample_html_error_body.html").read_bytes()
    kind = verify_content(
        body_head=body, total_bytes=len(body), expected_extension=".html", min_bytes=10
    )
    assert kind == "html"


def test_undersized_file_fails():
    body = b"tiny"
    with pytest.raises(VerificationError) as exc_info:
        verify_content(body_head=body, total_bytes=len(body), expected_extension=".xml", min_bytes=1000)
    assert exc_info.value.status == "FAIL-SIZE"


@pytest.mark.parametrize(
    ("head", "expected_kind"),
    [
        (b"%PDF-1.4\n...", "pdf"),
        (b"PK\x03\x04\x14\x00", "zip"),
        (b"\x1f\x8b\x08\x00", "gzip"),
        (b"<?xml version='1.0'?>", "xml"),
        (b"<!DOCTYPE html><html>", "html"),
        (b"not a recognizable format", None),
    ],
)
def test_sniff_content_kind(head, expected_kind):
    assert sniff_content_kind(head) == expected_kind

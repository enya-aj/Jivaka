"""Soft-404 defense: HTTP 200 alone doesn't prove the real file was served.
zoac-zeus-jivaka's own research documented nlmpubs.nlm.nih.gov returning
HTTP 200 with an HTML error body for nonexistent asciimesh/*.bin paths -
the two checks here (magic-byte sniff, size floor) are exactly the "check
content-type and file size, never trust status code alone" rule that
research recommended, kept independent of the HTTP Content-Type header
itself since servers are often inconsistent about it.
"""

from typing import Optional

_MAGIC_PREFIXES: list[tuple[bytes, str]] = [
    (b"%PDF", "pdf"),
    (b"PK\x03\x04", "zip"),  # also zip-based formats: .xlsx, .jar, .docx
    (b"\x1f\x8b", "gzip"),
]

_HTML_MARKERS = (b"<!doctype html", b"<html")


def sniff_content_kind(head: bytes) -> Optional[str]:
    """Best-effort content sniff from the first bytes of a response body.
    Returns a short kind label ("pdf", "zip", "gzip", "html", "xml") or
    None if inconclusive."""
    for prefix, kind in _MAGIC_PREFIXES:
        if head.startswith(prefix):
            return kind

    stripped = head.lstrip()[:512].lower()
    if any(marker in stripped for marker in _HTML_MARKERS):
        return "html"
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<"):
        return "xml"
    return None


class VerificationError(Exception):
    """Raised when downloaded content fails verification. `status` matches
    one of the manifest's FAIL-* status values."""

    def __init__(self, reason: str, status: str):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def verify_content(
    *,
    body_head: bytes,
    total_bytes: int,
    expected_extension: Optional[str],
    min_bytes: int,
) -> Optional[str]:
    """Verifies downloaded content. Raises VerificationError on a hard
    failure (soft-404, undersized). Returns a sniffed content kind (or
    None) for informational logging when verification passes."""
    if total_bytes < min_bytes:
        raise VerificationError(
            f"size {total_bytes} bytes is below the {min_bytes}-byte floor", "FAIL-SIZE"
        )

    sniffed = sniff_content_kind(body_head)
    expects_html = expected_extension in (".html", ".htm")
    if sniffed == "html" and not expects_html:
        raise VerificationError(
            "response body looks like an HTML error page, not the expected file (soft-404)",
            "FAIL-HTML",
        )

    return sniffed

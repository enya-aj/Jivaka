import hashlib
import time
from pathlib import Path
from typing import Optional

import httpx

from jivaka_corpus_fetch.models import ManifestRow, SourceEntry
from jivaka_corpus_fetch.verify import VerificationError, verify_content

ARCHIVE_ROOT = Path(__file__).resolve().parent.parent / "archive"

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = [2, 4, 8]
HEAD_SNIFF_BYTES = 2048


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def already_verified(entry: SourceEntry, existing_row: Optional[ManifestRow]) -> bool:
    """True if the manifest already has an OK row for this entry and the
    file on disk still matches its recorded hash - lets re-runs skip work
    without needing a --force flag."""
    if existing_row is None or existing_row.status != "OK" or not entry.dest:
        return False
    dest_path = ARCHIVE_ROOT / entry.dest
    if not dest_path.exists():
        return False
    return sha256_of_file(dest_path) == existing_row.sha256_12


def skip_row(entry: SourceEntry) -> ManifestRow:
    """For fetchable=false entries - this is the only code path they ever
    reach; they are never passed to fetch_one, so no login/CAPTCHA
    automation is structurally possible."""
    reason_lower = (entry.skip_reason or "").lower()
    if "dead" in reason_lower or "404" in reason_lower:
        status = "SKIP-DEAD"
    elif "cloudflare" in reason_lower or "blocked" in reason_lower or "unreachable" in reason_lower:
        status = "SKIP-BLOCKED"
    else:
        status = "SKIP-GATED"
    return ManifestRow(
        status=status,
        path="-",
        url=entry.url,
        category=entry.category,
        license_class=entry.license_class,
        name=entry.name,
        notes=entry.skip_reason or "",
    )


def fetch_one(entry: SourceEntry, client: httpx.Client) -> ManifestRow:
    """Downloads one source entry, verifying content before accepting it.
    Never raises - always returns a ManifestRow so a single bad source
    can't abort a run over the rest of the registry."""
    dest_path = ARCHIVE_ROOT / entry.dest
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest_path.with_name(dest_path.name + ".part")

    last_error: Optional[Exception] = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            with client.stream("GET", entry.url, follow_redirects=True) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "-")
                http_status = str(response.status_code)

                total_bytes = 0
                head_bytes = b""
                with open(part_path, "wb") as f:
                    for chunk in response.iter_bytes():
                        if len(head_bytes) < HEAD_SNIFF_BYTES:
                            head_bytes += chunk[: HEAD_SNIFF_BYTES - len(head_bytes)]
                        f.write(chunk)
                        total_bytes += len(chunk)

            verify_content(
                body_head=head_bytes,
                total_bytes=total_bytes,
                expected_extension=entry.expected_extension,
                min_bytes=entry.min_bytes,
            )

            part_path.replace(dest_path)
            sha = sha256_of_file(dest_path)
            return ManifestRow(
                status="OK",
                bytes=total_bytes,
                sha256_12=sha,
                path=entry.dest,
                url=entry.url,
                category=entry.category,
                license_class=entry.license_class,
                name=entry.name,
                http_status=http_status,
                content_type=content_type,
            )

        except VerificationError as exc:
            part_path.unlink(missing_ok=True)
            return ManifestRow(
                status=exc.status,
                path="-",
                url=entry.url,
                category=entry.category,
                license_class=entry.license_class,
                name=entry.name,
                notes=exc.reason,
            )
        except (httpx.HTTPError, OSError) as exc:
            last_error = exc
            part_path.unlink(missing_ok=True)
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF_SECONDS[attempt])
                continue

    return ManifestRow(
        status="FAIL",
        path="-",
        url=entry.url,
        category=entry.category,
        license_class=entry.license_class,
        name=entry.name,
        notes=f"failed after {RETRY_ATTEMPTS} attempts: {last_error}",
    )

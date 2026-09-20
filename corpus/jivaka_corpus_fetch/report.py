from pathlib import Path

from jivaka_corpus_fetch.models import ManifestRow

DEFAULT_REPORT_PATH = Path(__file__).resolve().parent.parent / "skip-report.md"

_GROUPS: list[tuple[str, str]] = [
    ("SKIP-GATED", "## Registration required"),
    ("SKIP-BLOCKED", "## Confirmed Cloudflare-blocked / unreachable"),
    ("SKIP-DEAD", "## Confirmed dead / 404"),
    ("FAIL-HTML", "## Failed content verification this run (soft-404 detected)"),
    ("FAIL-SIZE", "## Failed content verification this run (undersized)"),
    ("FAIL-TYPE", "## Failed content verification this run (unexpected type)"),
    ("FAIL", "## Other fetch failures"),
]


def generate_report(rows: dict[str, ManifestRow]) -> str:
    """Regenerated fully from scratch each run (not appended) - always
    reflects the current manifest, not run history."""
    lines = ["# Corpus fetch — skip report", ""]
    lines.append(
        "Sources that were not downloaded, grouped by why. Regenerated on every "
        "`fetch.py run`/`fetch.py report` — this file always reflects the current "
        "manifest, not a running history."
    )
    lines.append("")

    any_rows = False
    for status, heading in _GROUPS:
        matching = sorted((r for r in rows.values() if r.status == status), key=lambda r: r.name)
        if not matching:
            continue
        any_rows = True
        lines.append(heading)
        lines.append("")
        for row in matching:
            lines.append(f"- **{row.name}** (`{row.category}`/`{row.license_class}`) — {row.url}")
            if row.notes:
                lines.append(f"  {row.notes}")
        lines.append("")

    if not any_rows:
        lines.append("Nothing to report — every registry entry either fetched OK or hasn't run yet.")
        lines.append("")

    ok_count = sum(1 for r in rows.values() if r.status == "OK")
    lines.append(f"---\n\n{ok_count} source(s) successfully fetched and verified.")
    return "\n".join(lines)


def write_report(rows: dict[str, ManifestRow], path: Path = DEFAULT_REPORT_PATH) -> None:
    path.write_text(generate_report(rows), encoding="utf-8")

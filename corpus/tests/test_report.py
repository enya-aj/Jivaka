from jivaka_corpus_fetch.models import ManifestRow
from jivaka_corpus_fetch.report import generate_report


def _row(name: str, status: str, **kwargs) -> ManifestRow:
    defaults = dict(
        status=status,
        url=f"https://example.org/{name}",
        category="vocabulary",
        license_class="green",
        name=name,
    )
    defaults.update(kwargs)
    return ManifestRow(**defaults)


def test_groups_by_status_taxonomy():
    rows = {
        "a": _row("a", "OK"),
        "b": _row("b", "SKIP-GATED", notes="needs UTS account"),
        "c": _row("c", "SKIP-BLOCKED", notes="Cloudflare"),
        "d": _row("d", "SKIP-DEAD", notes="404"),
        "e": _row("e", "FAIL-HTML", notes="soft-404"),
        "f": _row("f", "FAIL", notes="timeout"),
    }

    report = generate_report(rows)

    assert "Registration required" in report
    assert "b" in report and "needs UTS account" in report
    assert "Confirmed Cloudflare-blocked" in report
    assert "c" in report
    assert "Confirmed dead" in report
    assert "d" in report
    assert "soft-404 detected" in report
    assert "e" in report
    assert "Other fetch failures" in report
    assert "f" in report
    assert "**a**" not in report  # the OK row is never listed as a bullet in any group


def test_ok_only_manifest_reports_nothing_to_do():
    rows = {"a": _row("a", "OK")}
    report = generate_report(rows)
    assert "Nothing to report" in report
    assert "1 source(s) successfully fetched" in report


def test_empty_manifest():
    report = generate_report({})
    assert "Nothing to report" in report
    assert "0 source(s)" in report

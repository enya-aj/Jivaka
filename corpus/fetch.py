"""Jivaka corpus data-acquisition CLI. Run with: python fetch.py <command>."""

from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table

from jivaka_corpus_fetch.downloader import already_verified, fetch_one, skip_row
from jivaka_corpus_fetch.manifest import read_manifest, write_manifest
from jivaka_corpus_fetch.registry import filter_entries, load_registry
from jivaka_corpus_fetch.report import write_report

app = typer.Typer(help="Jivaka corpus data-acquisition CLI")
console = Console()


@app.command()
def run(
    category: Optional[str] = typer.Option(None, help="vocabulary | corpus | patient-record"),
    license_class: Optional[str] = typer.Option(None, "--license-class", help="green | amber | red"),
    names: Optional[str] = typer.Option(None, help="Comma-separated registry entry names to restrict to"),
    limit: Optional[int] = typer.Option(None, help="Only process the first N matching entries"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan, touch no network/disk"),
) -> None:
    """Fetch sources from the registry, skipping anything not fetchable."""
    entries = filter_entries(load_registry(), category=category, license_class=license_class)
    if names:
        wanted = {n.strip() for n in names.split(",")}
        entries = [e for e in entries if e.name in wanted]
    if limit is not None:
        entries = entries[:limit]

    if dry_run:
        table = Table(title=f"Dry run: {len(entries)} entries")
        table.add_column("name")
        table.add_column("category")
        table.add_column("license")
        table.add_column("fetchable")
        for e in entries:
            table.add_row(e.name, e.category, e.license_class, "yes" if e.fetchable else "no")
        console.print(table)
        return

    rows = read_manifest()
    client = httpx.Client(timeout=120.0)

    fetched = skipped = reused = failed = 0
    for entry in entries:
        if not entry.fetchable:
            rows[entry.name] = skip_row(entry)
            skipped += 1
            console.print(f"[yellow]SKIP[/yellow] {entry.name}: {entry.skip_reason}")
            continue

        if already_verified(entry, rows.get(entry.name)):
            reused += 1
            console.print(f"[dim]OK (cached)[/dim] {entry.name}")
            continue

        row = fetch_one(entry, client)
        rows[entry.name] = row
        if row.status == "OK":
            fetched += 1
            console.print(f"[green]OK[/green] {entry.name} ({row.bytes} bytes)")
        else:
            failed += 1
            console.print(f"[red]{row.status}[/red] {entry.name}: {row.notes}")

    write_manifest(rows)
    write_report(rows)
    console.print(
        f"\n[bold]Done.[/bold] fetched={fetched} reused={reused} skipped={skipped} failed={failed}"
    )
    console.print("See skip-report.md for anything that needs manual follow-up.")


@app.command()
def report() -> None:
    """Regenerate skip-report.md from the existing manifest without fetching anything."""
    rows = read_manifest()
    write_report(rows)
    console.print(f"Wrote skip-report.md from {len(rows)} manifest row(s).")


if __name__ == "__main__":
    app()

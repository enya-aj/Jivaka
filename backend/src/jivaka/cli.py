from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from jivaka.db.falkor_client import get_graph
from jivaka.db.graph_reader import get_document_graph
from jivaka.ingestion.models import DocType
from jivaka.ingestion.pipeline import ingest_document

app = typer.Typer(help="Jivaka ingestion CLI")
console = Console()


@app.command()
def ingest(
    path: Path = typer.Argument(..., exists=True, readable=True, help="Document to ingest"),
    doc_type: DocType = typer.Option(..., "--doc-type", help="patient_record or textbook"),
    print_graph: bool = typer.Option(False, "--print-graph", help="Print the resulting graph"),
) -> None:
    """Ingest one document into the trinity graph."""
    graph = get_graph()
    result = ingest_document(path=path, doc_type=doc_type, graph=graph)

    table = Table(title=f"Ingested {result.document.filename}")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("doc_id", result.document.id)
    table.add_row("chunks", str(result.chunk_count))
    table.add_row("entities", str(result.entity_count))
    table.add_row("relations", str(result.relation_count))
    table.add_row("definitions matched", str(result.definitions_matched))
    table.add_row("definitions missing", str(result.definitions_missing))
    table.add_row("OCR pages used", str(result.ocr_pages_used))
    table.add_row("elapsed (s)", f"{result.elapsed_seconds:.2f}")
    console.print(table)

    if print_graph:
        _print_graph(result.document.id, graph)


@app.command(name="graph-show")
def graph_show(doc_id: str = typer.Argument(..., help="Document id")) -> None:
    """Print a document's trinity subgraph."""
    graph = get_graph()
    _print_graph(doc_id, graph)


def _print_graph(doc_id: str, graph) -> None:
    dump = get_document_graph(graph, doc_id)
    if dump is None:
        console.print(f"[red]No document found with id {doc_id}[/red]")
        raise typer.Exit(code=1)

    tree = Tree(f"Document {dump['document'].get('filename')} ({doc_id})")
    for entry in dump["chunks"]:
        chunk = entry["chunk"]
        chunk_branch = tree.add(f"Chunk #{chunk.get('order')}: {_truncate(chunk.get('text', ''))}")
        for e in entry["entities"]:
            entity = e["entity"]
            definition = e["definition"]
            label = f"[{entity.get('entity_type')}] {entity.get('name')}"
            if definition:
                label += f" -> {_truncate(definition.get('definition_text', ''), 80)}"
            else:
                label += " -> (no definition)"
            chunk_branch.add(label)

    console.print(tree)

    if dump["relations"]:
        rel_table = Table(title="Entity relations")
        rel_table.add_column("source")
        rel_table.add_column("relation")
        rel_table.add_column("target")
        for rel in dump["relations"]:
            rel_table.add_row(rel["source_name"], rel["relation_type"], rel["target_name"])
        console.print(rel_table)


def _truncate(text: str, length: int = 60) -> str:
    text = text.replace("\n", " ").strip()
    return text if len(text) <= length else text[: length - 1] + "…"


if __name__ == "__main__":
    app()

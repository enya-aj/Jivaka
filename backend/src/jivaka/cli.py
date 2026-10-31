import enum
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from jivaka.db.falkor_client import get_graph
from jivaka.db.graph_reader import get_document_graph
from jivaka.ingestion.pipeline import ingest_document

app = typer.Typer(help="Jivaka ingestion CLI")
console = Console()


class DocTypeArg(str, enum.Enum):
    """Typer/Click can't build a choice option from `typing.Literal` (as used
    by jivaka.ingestion.models.DocType) - a plain str Enum is its documented
    way to get the same CLI validation."""

    patient_record = "patient_record"
    textbook = "textbook"


@app.command()
def ingest(
    path: Path = typer.Argument(..., exists=True, readable=True, help="Document to ingest"),
    doc_type: DocTypeArg = typer.Option(..., "--doc-type", help="patient_record or textbook"),
    print_graph: bool = typer.Option(False, "--print-graph", help="Print the resulting graph"),
) -> None:
    """Ingest one document into the trinity graph."""
    graph = get_graph()
    result = ingest_document(path=path, doc_type=doc_type.value, graph=graph)

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


@app.command()
def ask(question: str = typer.Argument(..., help="Question to answer from the graph")) -> None:
    """Answer one question from the graph, the same way the chat page does."""
    from jivaka.chat.service import ChatService
    from jivaka.config import get_settings

    service = ChatService(get_graph(), top_k=get_settings().retrieval_top_k)
    sources: list[dict] = []
    for event in service.answer([{"role": "user", "content": question}]):
        if event.type == "sources":
            sources = event.data
        elif event.type == "token":
            console.print(event.data, end="", markup=False, highlight=False)
    console.print()

    if sources:
        table = Table(title="Sources")
        table.add_column("#")
        table.add_column("source")
        table.add_column("page")
        table.add_column("score")
        table.add_column("text")
        for source in sources:
            page = source["page_number"]
            table.add_row(
                str(source["number"]),
                source["source_label"],
                "" if page is None else str(page),
                f"{source['score']:.1f}",
                _truncate(source["text"]),
            )
        console.print(table)


@app.command(name="deid-backfill")
def deid_backfill() -> None:
    """De-identify patient records ingested before de-identification existed,
    in place, and give each one a pseudonym."""
    from jivaka.config import get_settings
    from jivaka.deid import build_deidentifier
    from jivaka.deid.backfill import deidentify_existing_patient_records

    settings = get_settings()
    deidentifier = build_deidentifier(settings.deid_use_ner, settings.deid_extra_id_patterns)
    results = deidentify_existing_patient_records(get_graph(), deidentifier)

    table = Table(title="Patient records")
    table.add_column("doc_id")
    table.add_column("pseudonym")
    table.add_column("chunks de-identified")
    table.add_column("entities removed")
    for r in results:
        table.add_row(r.doc_id, r.pseudonym, str(r.chunks_updated), str(r.entities_removed))
    console.print(table)


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

from falkordb import Graph

# (label, property) pairs to index. Keep in sync with the fields graph_writer.py
# and any future query code actually filter/match on.
INDEXES = [
    ("Document", "id"),
    ("Document", "source_hash"),
    ("Chunk", "doc_id"),
    ("Entity", "doc_id"),
    ("Entity", "normalized_name"),
    ("Definition", "normalized_term"),
    ("Definition", "source_id"),
]


def ensure_schema(graph: Graph) -> None:
    """Idempotently creates the indexes the ingestion pipeline relies on.

    FalkorDB's CREATE INDEX has no IF NOT EXISTS clause, so re-running this
    raises an "already indexed" error on repeat calls, which we swallow.
    """
    for label, prop in INDEXES:
        try:
            graph.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
        except Exception as exc:  # noqa: BLE001 - FalkorDB raises a generic exception
            if "already indexed" not in str(exc).lower():
                raise

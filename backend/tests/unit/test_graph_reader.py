from jivaka.db.graph_reader import list_documents


class FakeNode:
    def __init__(self, properties):
        self.properties = properties


class FakeResult:
    def __init__(self, result_set):
        self.result_set = result_set


class FakeGraph:
    """Captures query calls and returns a canned result set."""

    def __init__(self, result_set):
        self._result_set = result_set
        self.calls: list[tuple[str, dict]] = []

    def query(self, cypher, params=None):
        self.calls.append((cypher, params or {}))
        return FakeResult(self._result_set)


def test_list_documents_returns_node_properties_in_order():
    docs = [
        {"id": "doc-1", "filename": "a.txt", "ingested_at": "2026-01-02T00:00:00Z"},
        {"id": "doc-2", "filename": "b.txt", "ingested_at": "2026-01-01T00:00:00Z"},
    ]
    graph = FakeGraph(result_set=[[FakeNode(d)] for d in docs])

    result = list_documents(graph, limit=10)

    assert result == docs
    cypher, params = graph.calls[0]
    assert "ORDER BY d.ingested_at DESC" in cypher
    assert params == {"limit": 10}


def test_list_documents_default_limit():
    graph = FakeGraph(result_set=[])

    result = list_documents(graph)

    assert result == []
    _, params = graph.calls[0]
    assert params["limit"] == 50

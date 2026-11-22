from jivaka.db.graph_reader import find_documents_by_hash, get_document_graph, list_documents


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


def test_find_documents_by_hash_filters_on_hash_and_doc_type():
    doc = {"id": "doc-1", "source_hash": "abc", "doc_type": "textbook", "ingestion_complete": True}
    graph = FakeGraph(result_set=[[FakeNode(doc)]])

    result = find_documents_by_hash(graph, "abc", "textbook")

    assert result == [doc]
    cypher, params = graph.calls[0]
    assert "source_hash: $source_hash" in cypher and "doc_type: $doc_type" in cypher
    assert params == {"source_hash": "abc", "doc_type": "textbook"}


class ScriptedGraph:
    """Returns the next canned result set for each query, in order."""

    def __init__(self, result_sets):
        self._result_sets = list(result_sets)
        self.calls: list[tuple[str, dict]] = []

    def query(self, cypher, params=None):
        self.calls.append((cypher, params or {}))
        return FakeResult(self._result_sets.pop(0))


def test_get_document_graph_with_max_chunks_reads_only_those_chunks():
    graph = ScriptedGraph(
        [
            [[FakeNode({"id": "doc-1", "filename": "big.txt"})]],
            [[5000]],
            [[FakeNode({"id": "c-0", "order": 0})], [FakeNode({"id": "c-1", "order": 1})]],
            [["c-0", FakeNode({"id": "e-a", "name": "A"})], ["c-1", FakeNode({"id": "e-b", "name": "B"})]],
            [["e-a", FakeNode({"definition_text": "def A"})]],
            [["e-a", "A", "TREATS", "e-b", "B"]],
        ]
    )

    dump = get_document_graph(graph, "doc-1", max_chunks=2)

    assert dump["total_chunks"] == 5000
    assert dump["truncated"] is True
    assert [c["chunk"]["id"] for c in dump["chunks"]] == ["c-0", "c-1"]
    assert dump["chunks"][0]["entities"][0]["definition"] == {"definition_text": "def A"}
    assert dump["chunks"][1]["entities"][0]["definition"] is None
    assert dump["relations"][0]["relation_type"] == "TREATS"
    chunk_query, chunk_params = graph.calls[2]
    assert "LIMIT $limit" in chunk_query and chunk_params["limit"] == 2
    assert graph.calls[3][1] == {"chunk_ids": ["c-0", "c-1"]}
    assert graph.calls[5][1] == {"entity_ids": ["e-a", "e-b"]}


def test_get_document_graph_not_truncated_when_all_chunks_fit():
    graph = ScriptedGraph(
        [
            [[FakeNode({"id": "doc-1"})]],
            [[1]],
            [[FakeNode({"id": "c-0", "order": 0})]],
            [],
            [],
            [],
        ]
    )

    dump = get_document_graph(graph, "doc-1", max_chunks=100)

    assert dump["truncated"] is False
    assert dump["total_chunks"] == 1

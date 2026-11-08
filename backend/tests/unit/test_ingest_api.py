import threading

from fastapi.testclient import TestClient

from jivaka.api import routes
from jivaka.api.jobs import get_job
from jivaka.ingestion.models import Document, IngestionResult
from jivaka.main import app


def _result(filename: str) -> IngestionResult:
    return IngestionResult(
        document=Document(filename=filename, doc_type="textbook", source_hash="h"),
        chunk_count=3,
        entity_count=0,
        relation_count=0,
        definitions_matched=0,
        definitions_missing=0,
        ocr_pages_used=0,
        elapsed_seconds=0.1,
    )


def _upload(client: TestClient, name: str = "notes.txt") -> str:
    response = client.post(
        "/ingest", files={"file": (name, b"some text", "text/plain")}, data={"doc_type": "textbook"}
    )
    assert response.status_code == 200
    return response.json()["job_id"]


def test_ingest_job_reports_document_and_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "get_settings", lambda: type("S", (), {"upload_dir": str(tmp_path)})())
    monkeypatch.setattr(routes, "get_graph", lambda: None)
    seen = {}

    def fake_ingest(path, doc_type, graph, on_progress, on_document):
        result = _result(path.name)
        on_document(result.document.id)
        seen["after_document"] = get_job(job_ids[0]).to_dict()
        on_progress(0, 3)
        on_progress(2, 3)
        seen["after_progress"] = get_job(job_ids[0]).to_dict()
        on_progress(3, 3)
        return result

    job_ids = []
    original_create = routes.job_registry.create_job

    def tracking_create(**kwargs):
        job = original_create(**kwargs)
        job_ids.append(job.id)
        return job

    monkeypatch.setattr(routes.job_registry, "create_job", tracking_create)
    monkeypatch.setattr(routes, "ingest_document", fake_ingest)
    client = TestClient(app)

    job_id = _upload(client)

    assert seen["after_document"]["status"] == "running"
    assert seen["after_document"]["filename"] == "notes.txt"
    assert seen["after_document"]["doc_id"]
    assert seen["after_progress"]["progress"] == "chunk 2/3"
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "succeeded"
    assert job["doc_id"] == job["result"]["document"]["id"]
    assert job_id in [j["id"] for j in client.get("/jobs").json()]


def test_cancel_stops_a_running_ingest(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "get_graph", lambda: None)
    started = threading.Event()
    release = threading.Event()

    def fake_ingest(path, doc_type, graph, on_progress, on_document):
        on_document("doc-1")
        started.set()
        release.wait(5)
        on_progress(1, 100)  # raises IngestionCancelled once cancel is requested
        raise AssertionError("should have been cancelled")

    monkeypatch.setattr(routes, "ingest_document", fake_ingest)
    client = TestClient(app)
    job = routes.job_registry.create_job(filename="Thesaurus.txt", doc_type="textbook")
    upload = tmp_path / "upload.txt"
    upload.write_text("x")

    worker = threading.Thread(target=routes._run_ingest_job, args=(job.id, upload, "textbook"))
    worker.start()
    assert started.wait(5)

    response = client.post(f"/jobs/{job.id}/cancel")
    assert response.json()["cancel_requested"] is True
    release.set()
    worker.join(5)

    final = client.get(f"/jobs/{job.id}").json()
    assert final["status"] == "cancelled"
    assert final["doc_id"] is None
    assert not upload.exists()


def test_cancel_unknown_job_is_404():
    assert TestClient(app).post("/jobs/nope/cancel").status_code == 404


def test_documents_carry_a_status(monkeypatch):
    monkeypatch.setattr(routes, "get_graph", lambda: None)
    monkeypatch.setattr(
        routes,
        "list_documents",
        lambda graph, limit: [
            {"id": "a", "ingestion_complete": True},
            {"id": "b", "ingestion_complete": False},
            {"id": "c"},
        ],
    )

    docs = TestClient(app).get("/documents").json()

    assert [d["status"] for d in docs] == ["done", "incomplete", "done"]


def test_ingest_rejects_unsupported_file_type(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "get_settings", lambda: type("S", (), {"upload_dir": str(tmp_path)})())
    client = TestClient(app)

    response = client.post(
        "/ingest", files={"file": ("slides.pptx", b"x", "application/octet-stream")}, data={"doc_type": "textbook"}
    )

    assert response.status_code == 400
    assert "Unsupported file type '.pptx'" in response.json()["detail"]
    assert list(tmp_path.iterdir()) == []


class _FakeDeleteGraph:
    def __init__(self, filename="doc.pdf"):
        self.filename = filename
        self.queries = []

    def query(self, cypher, params=None):
        self.queries.append(cypher)
        rows = [] if self.filename is None else [[self.filename]]
        return type("R", (), {"result_set": rows if "RETURN d.filename" in cypher else []})()


def _upload_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "get_settings", lambda: type("S", (), {"upload_dir": str(tmp_path)})())


def test_delete_removes_a_document_and_its_upload(monkeypatch, tmp_path):
    _upload_dir(monkeypatch, tmp_path)
    (tmp_path / "abc_doc.pdf").write_bytes(b"x")
    (tmp_path / "other.pdf").write_bytes(b"x")
    graph = _FakeDeleteGraph(filename="abc_doc.pdf")
    monkeypatch.setattr(routes, "get_graph", lambda: graph)

    response = TestClient(app).delete("/documents/doc-1")

    assert response.status_code == 200
    assert any("DETACH DELETE d" in q for q in graph.queries)
    assert [p.name for p in tmp_path.iterdir()] == ["other.pdf"]


def test_delete_refuses_missing_and_running_documents(monkeypatch, tmp_path):
    _upload_dir(monkeypatch, tmp_path)
    client = TestClient(app)
    graph = _FakeDeleteGraph(filename=None)
    monkeypatch.setattr(routes, "get_graph", lambda: graph)
    assert client.delete("/documents/doc-1").status_code == 404
    assert not any("DETACH DELETE" in q for q in graph.queries)

    job = routes.job_registry.create_job(filename="big.pdf", doc_type="textbook")
    routes.job_registry.update_job(job.id, status=routes.job_registry.JobStatus.running, doc_id="doc-running")
    graph = _FakeDeleteGraph()
    monkeypatch.setattr(routes, "get_graph", lambda: graph)
    response = client.delete("/documents/doc-running")
    assert response.status_code == 409
    assert "Cancel" in response.json()["detail"]
    assert not any("DETACH DELETE" in q for q in graph.queries)
    routes.job_registry.update_job(job.id, status=routes.job_registry.JobStatus.cancelled)


def test_dismiss_removes_finished_jobs_only():
    client = TestClient(app)
    registry = routes.job_registry
    cancelled = registry.create_job(filename="a.txt", doc_type="textbook")
    registry.update_job(cancelled.id, status=registry.JobStatus.cancelled)
    running = registry.create_job(filename="b.txt", doc_type="textbook")
    registry.update_job(running.id, status=registry.JobStatus.running)

    assert client.delete(f"/jobs/{cancelled.id}").status_code == 200
    assert registry.get_job(cancelled.id) is None
    assert client.delete(f"/jobs/{running.id}").status_code == 409
    assert registry.get_job(running.id) is not None
    assert client.delete("/jobs/nope").status_code == 404
    registry.update_job(running.id, status=registry.JobStatus.cancelled)


def test_ingest_llm_lists_the_model_for_each_doc_type(monkeypatch):
    monkeypatch.setattr(routes, "extraction_llm", lambda doc_type: {"provider": "ollama", "model": f"m-{doc_type}"})
    response = TestClient(app).get("/ingest/llm")
    assert response.status_code == 200
    assert response.json() == {
        "patient_record": {"provider": "ollama", "model": "m-patient_record"},
        "textbook": {"provider": "ollama", "model": "m-textbook"},
    }


def test_ingest_job_records_its_extraction_model(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "get_settings", lambda: type("S", (), {"upload_dir": str(tmp_path)})())
    monkeypatch.setattr(routes, "extraction_llm", lambda doc_type: {"provider": "openrouter", "model": "v/m"})
    monkeypatch.setattr(routes, "_run_ingest_job", lambda *args: None)
    job_id = _upload(TestClient(app))
    assert get_job(job_id).to_dict()["llm"] == {"provider": "openrouter", "model": "v/m"}

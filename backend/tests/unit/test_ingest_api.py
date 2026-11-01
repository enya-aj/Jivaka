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

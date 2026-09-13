import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from jivaka.api import jobs as job_registry
from jivaka.config import get_settings
from jivaka.db.falkor_client import get_graph
from jivaka.db.graph_reader import get_document_graph, list_documents
from jivaka.ingestion.models import DocType
from jivaka.ingestion.pipeline import ingest_document

router = APIRouter()


@router.post("/ingest")
async def ingest_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    doc_type: DocType = Form(...),
) -> dict:
    """Saves the upload and kicks off ingestion as a background job rather
    than blocking the request - ingestion can take minutes (LLM calls per
    chunk), so poll GET /jobs/{job_id} for status/progress/result instead of
    waiting on this response."""
    settings = get_settings()
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{uuid4().hex}_{file.filename}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    job = job_registry.create_job()
    background_tasks.add_task(_run_ingest_job, job.id, dest, doc_type)
    return {"job_id": job.id, "status": job.status}


def _run_ingest_job(job_id: str, path: Path, doc_type: DocType) -> None:
    # Runs in FastAPI's threadpool (BackgroundTasks runs sync callables off
    # the event loop), so the blocking ingest_document call here doesn't
    # stall other requests.
    job_registry.update_job(job_id, status=job_registry.JobStatus.running)

    def on_progress(done: int, total: int) -> None:
        job_registry.update_job(job_id, progress=f"chunk {done}/{total}")

    try:
        graph = get_graph()
        result = ingest_document(path=path, doc_type=doc_type, graph=graph, on_progress=on_progress)
        job_registry.update_job(job_id, status=job_registry.JobStatus.succeeded, result=result)
    except Exception as exc:
        job_registry.update_job(job_id, status=job_registry.JobStatus.failed, error=str(exc))


@router.get("/jobs/{job_id}")
async def get_job_endpoint(job_id: str) -> dict:
    job = job_registry.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id,
        "status": job.status,
        "progress": job.progress,
        "result": job.result,
        "error": job.error,
    }


@router.get("/documents")
async def list_documents_endpoint(limit: int = 50) -> list[dict]:
    graph = get_graph()
    return list_documents(graph, limit=limit)


@router.get("/documents/{doc_id}/graph")
async def get_graph_endpoint(doc_id: str) -> dict:
    graph = get_graph()
    dump = get_document_graph(graph, doc_id)
    if dump is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return dump

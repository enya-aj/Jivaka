import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from jivaka.api import jobs as job_registry
from jivaka.config import get_settings
from jivaka.db.falkor_client import get_graph
from jivaka.db.graph_reader import get_document_graph, list_documents
from jivaka.ingestion.models import DocType
from jivaka.ingestion.pipeline import IngestionCancelled, ingest_document

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

    job = job_registry.create_job(filename=file.filename or dest.name, doc_type=doc_type)
    background_tasks.add_task(_run_ingest_job, job.id, dest, doc_type)
    return {"job_id": job.id, "status": job.status}


def _run_ingest_job(job_id: str, path: Path, doc_type: DocType) -> None:
    # Runs in FastAPI's threadpool (BackgroundTasks runs sync callables off
    # the event loop), so the blocking ingest_document call here doesn't
    # stall other requests.
    job = job_registry.get_job(job_id)
    if job is None:
        return
    if job.cancel_requested:
        path.unlink(missing_ok=True)
        job_registry.update_job(job_id, status=job_registry.JobStatus.cancelled, progress="")
        return
    job_registry.update_job(job_id, status=job_registry.JobStatus.running, progress="reading file")

    def check_cancel() -> None:
        if job.cancel_requested:
            raise IngestionCancelled()

    def on_document(doc_id: str) -> None:
        job_registry.update_job(job_id, doc_id=doc_id, progress="splitting into chunks")
        check_cancel()

    def on_progress(done: int, total: int) -> None:
        job_registry.update_job(job_id, progress=f"chunk {done}/{total}")
        check_cancel()

    try:
        graph = get_graph()
        result = ingest_document(
            path=path, doc_type=doc_type, graph=graph, on_progress=on_progress, on_document=on_document
        )
        if result.duplicate:
            # Already ingested from an identical file, so this copy isn't needed.
            path.unlink(missing_ok=True)
        job_registry.update_job(
            job_id, status=job_registry.JobStatus.succeeded, result=result, doc_id=result.document.id
        )
    except IngestionCancelled:
        path.unlink(missing_ok=True)
        job_registry.update_job(job_id, status=job_registry.JobStatus.cancelled, doc_id=None)
    except Exception as exc:
        job_registry.update_job(job_id, status=job_registry.JobStatus.failed, error=str(exc))


@router.get("/jobs")
async def list_jobs_endpoint() -> list[dict]:
    """Running ingests plus ones that finished in the last day, newest first."""
    return [job.to_dict() for job in job_registry.list_jobs()]


@router.get("/jobs/{job_id}")
async def get_job_endpoint(job_id: str) -> dict:
    job = job_registry.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@router.post("/jobs/{job_id}/cancel")
async def cancel_job_endpoint(job_id: str) -> dict:
    job = job_registry.request_cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@router.get("/documents")
async def list_documents_endpoint(limit: int = 50) -> list[dict]:
    graph = get_graph()
    docs = list_documents(graph, limit=limit)
    for doc in docs:
        # ingestion_complete is false while a run is in progress or after one
        # was interrupted; documents older than that flag don't carry it.
        doc["status"] = "incomplete" if doc.get("ingestion_complete") is False else "done"
    return docs


@router.get("/documents/{doc_id}/graph")
async def get_graph_endpoint(doc_id: str) -> dict:
    graph = get_graph()
    dump = get_document_graph(graph, doc_id)
    if dump is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return dump

import enum
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from jivaka.ingestion.models import IngestionResult


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    # Very similar to a document already ingested: waits for the uploader
    # to choose "ingest anyway" (POST /jobs/{id}/confirm) or cancel.
    needs_confirmation = "needs_confirmation"


ACTIVE_STATUSES = (JobStatus.queued, JobStatus.running)


@dataclass
class Job:
    id: str
    filename: str = ""
    doc_type: str = ""
    # The LLM extracting this document, as {"provider", "model"}, fixed when
    # the job is created (settings only change on a backend restart).
    model: Optional[dict] = None
    status: JobStatus = JobStatus.queued
    progress: str = ""
    # Set once the pipeline has written the Document node, so the page can
    # match this job to its (still incomplete) row in GET /documents.
    doc_id: Optional[str] = None
    cancel_requested: bool = False
    result: Optional[IngestionResult] = None
    error: Optional[str] = None
    # Who uploaded the file (Google email and name).
    uploaded_by: str = ""
    uploaded_by_name: str = ""
    # The saved upload, kept while waiting for confirmation.
    path: Optional[str] = None
    # Near-duplicate documents found, for needs_confirmation.
    similar: list = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "doc_type": self.doc_type,
            "model": self.model,
            "status": self.status,
            "progress": self.progress,
            "doc_id": self.doc_id,
            "cancel_requested": self.cancel_requested,
            "result": self.result,
            "error": self.error,
            "uploaded_by": self.uploaded_by,
            "uploaded_by_name": self.uploaded_by_name,
            "similar": self.similar,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# In-memory only: fine for the single backend instance this stack runs today,
# but a restart loses job history (and kills running ingests, whose documents
# then show up as "interrupted") and a second replica wouldn't see it.
_jobs: dict[str, Job] = {}
_lock = threading.Lock()

# Finished jobs are dropped from the listing after this long; the documents
# themselves stay in the graph.
FINISHED_JOB_TTL_SECONDS = 24 * 3600


def create_job(
    filename: str = "",
    doc_type: str = "",
    model: Optional[dict] = None,
    uploaded_by: str = "",
    uploaded_by_name: str = "",
    path: Optional[str] = None,
) -> Job:
    job = Job(
        id=str(uuid.uuid4()),
        filename=filename,
        doc_type=doc_type,
        model=model,
        uploaded_by=uploaded_by,
        uploaded_by_name=uploaded_by_name,
        path=path,
    )
    with _lock:
        _jobs[job.id] = job
    return job


def get_job(job_id: str) -> Optional[Job]:
    return _jobs.get(job_id)


def update_job(job_id: str, **fields) -> None:
    job = _jobs.get(job_id)
    if job is None:
        return
    for key, value in fields.items():
        setattr(job, key, value)
    job.updated_at = time.time()


def list_jobs(now: Optional[float] = None) -> list[Job]:
    """Active jobs plus jobs that finished within the TTL, newest first."""
    now = time.time() if now is None else now
    with _lock:
        for job_id in [
            j.id
            for j in _jobs.values()
            if j.status not in ACTIVE_STATUSES and now - j.updated_at > FINISHED_JOB_TTL_SECONDS
        ]:
            expired = _jobs.pop(job_id)
            if expired.status == JobStatus.needs_confirmation and expired.path:
                # Nobody chose; drop the upload that was waiting.
                Path(expired.path).unlink(missing_ok=True)
        jobs = list(_jobs.values())
    return sorted(jobs, key=lambda j: j.created_at, reverse=True)


def request_cancel(job_id: str) -> Optional[Job]:
    """Asks a queued/running job to stop. The ingest checks the flag between
    chunks, so it stops after the chunk it is on and removes what it wrote."""
    job = _jobs.get(job_id)
    if job is None:
        return None
    if job.status in ACTIVE_STATUSES:
        update_job(job_id, cancel_requested=True)
    return job


def dismiss_job(job_id: str) -> Optional[Job]:
    """Removes a finished (succeeded, failed or cancelled) job from the
    listing. Returns the job, or None if there is no such job. Active jobs
    are left in place and returned unchanged; cancel them first."""
    with _lock:
        job = _jobs.get(job_id)
        if job is not None and job.status not in ACTIVE_STATUSES:
            del _jobs[job_id]
    return job

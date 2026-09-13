import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from jivaka.ingestion.models import IngestionResult


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


@dataclass
class Job:
    id: str
    status: JobStatus = JobStatus.queued
    progress: str = ""
    result: Optional[IngestionResult] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


# In-memory only: fine for the single backend instance this stack runs today,
# but a restart loses job history and a second replica wouldn't see it.
_jobs: dict[str, Job] = {}


def create_job() -> Job:
    job = Job(id=str(uuid.uuid4()))
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

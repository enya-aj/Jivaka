from jivaka.api.jobs import JobStatus, create_job, get_job, update_job


def test_create_job_starts_queued_and_is_retrievable():
    job = create_job()

    assert job.status == JobStatus.queued
    assert get_job(job.id) is job


def test_update_job_sets_fields_and_bumps_updated_at():
    job = create_job()
    original_updated_at = job.updated_at

    update_job(job.id, status=JobStatus.running, progress="chunk 1/3")

    assert job.status == JobStatus.running
    assert job.progress == "chunk 1/3"
    assert job.updated_at >= original_updated_at


def test_update_unknown_job_is_a_noop():
    update_job("does-not-exist", status=JobStatus.failed)  # must not raise

    assert get_job("does-not-exist") is None


def test_get_job_unknown_returns_none():
    assert get_job("nope-nope-nope") is None

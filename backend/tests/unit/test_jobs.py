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


def test_list_jobs_newest_first_and_drops_old_finished_jobs():
    from jivaka.api import jobs

    old = create_job(filename="old.pdf")
    update_job(old.id, status=JobStatus.succeeded)
    old.updated_at -= jobs.FINISHED_JOB_TTL_SECONDS + 1
    stale_running = create_job(filename="long.txt")
    update_job(stale_running.id, status=JobStatus.running)
    stale_running.updated_at -= jobs.FINISHED_JOB_TTL_SECONDS + 1
    newest = create_job(filename="new.pdf")
    newest.created_at += 1

    listed = jobs.list_jobs()

    ids = [j.id for j in listed]
    assert old.id not in ids
    assert stale_running.id in ids  # still running, so kept however long it takes
    assert ids.index(newest.id) < ids.index(stale_running.id)
    assert get_job(old.id) is None


def test_request_cancel_flags_active_jobs_only():
    from jivaka.api.jobs import request_cancel

    running = create_job()
    update_job(running.id, status=JobStatus.running)
    finished = create_job()
    update_job(finished.id, status=JobStatus.failed)

    assert request_cancel(running.id).cancel_requested is True
    assert request_cancel(finished.id).cancel_requested is False
    assert request_cancel("does-not-exist") is None

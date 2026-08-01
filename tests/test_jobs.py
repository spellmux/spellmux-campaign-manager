from campaign_manager.database import configure_database, session_factory
from campaign_manager.jobs import claim_next_job, complete_job
from campaign_manager.models import Base, Job, JobStatus, ProcessingControl


def test_worker_claims_only_supported_job_kinds(tmp_path) -> None:
    engine = configure_database(f"sqlite:///{tmp_path / 'jobs.db'}")
    Base.metadata.create_all(engine)
    with session_factory()() as database:
        database.add_all(
            [
                Job(kind="transcription", status=JobStatus.QUEUED.value, payload={}),
                Job(kind="noop", status=JobStatus.QUEUED.value, payload={}),
            ]
        )
        database.commit()

        job = claim_next_job(database, {"noop"})

        assert job is not None
        assert job.kind == "noop"
        assert job.status == JobStatus.RUNNING.value
        assert job.attempts == 1
        complete_job(database, job)
        assert job.status == JobStatus.SUCCEEDED.value

        assert claim_next_job(database, {"noop"}) is None


def test_worker_respects_priority_and_processing_pause(tmp_path) -> None:
    engine = configure_database(f"sqlite:///{tmp_path / 'priority.db'}")
    Base.metadata.create_all(engine)
    with session_factory()() as database:
        database.add_all([
            ProcessingControl(kind="analysis", paused=True),
            Job(kind="analysis", status="queued", priority=100, payload={}),
            Job(kind="noop", status="queued", priority=0, payload={}),
            Job(kind="noop", status="queued", priority=50, payload={}),
        ])
        database.commit()

        first = claim_next_job(database, {"analysis", "noop"})
        assert first is not None
        assert first.kind == "noop"
        assert first.priority == 50

        complete_job(database, first)
        control = database.get(ProcessingControl, "analysis")
        control.paused = False
        database.commit()
        second = claim_next_job(database, {"analysis", "noop"})
        assert second is not None
        assert second.kind == "analysis"


def test_completed_job_honors_cancellation_requested_during_work(tmp_path) -> None:
    engine = configure_database(f"sqlite:///{tmp_path / 'cancel.db'}")
    Base.metadata.create_all(engine)
    with session_factory()() as database:
        job = Job(kind="noop", status="running", payload={})
        database.add(job)
        database.commit()
        job.cancel_requested = True
        database.commit()

        complete_job(database, job)

        assert job.status == JobStatus.CANCELLED.value


def test_heavy_jobs_share_one_compute_lane(tmp_path) -> None:
    engine = configure_database(f"sqlite:///{tmp_path / 'lane.db'}")
    Base.metadata.create_all(engine)
    with session_factory()() as database:
        database.add_all([
            ProcessingControl(kind="__compute_lane__", paused=False),
            Job(kind="transcription", status="running", payload={}),
            Job(kind="analysis", status="queued", priority=100, payload={}),
            Job(kind="noop", status="queued", priority=0, payload={}),
        ])
        database.commit()

        job = claim_next_job(database, {"analysis", "noop"})

        assert job is not None
        assert job.kind == "noop"

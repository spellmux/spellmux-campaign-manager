from campaign_manager.database import configure_database, session_factory
from campaign_manager.jobs import claim_next_job, complete_job
from campaign_manager.models import Base, Job, JobStatus


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

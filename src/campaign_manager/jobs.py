"""Transactional durable job claiming."""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.orm import Session

from campaign_manager.models import Job, JobStatus, utc_now


def claim_next_job(database: Session, supported_kinds: Collection[str]) -> Job | None:
    if not supported_kinds:
        return None
    statement = (
        select(Job)
        .where(Job.status == JobStatus.QUEUED.value, Job.kind.in_(supported_kinds))
        .order_by(Job.created_at, Job.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = database.scalar(statement)
    if job is None:
        database.rollback()
        return None
    job.status = JobStatus.RUNNING.value
    job.attempts += 1
    job.updated_at = utc_now()
    database.commit()
    database.refresh(job)
    return job


def complete_job(database: Session, job: Job) -> None:
    job.status = JobStatus.SUCCEEDED.value
    job.error = None
    job.updated_at = utc_now()
    database.commit()


def fail_job(database: Session, job: Job, error: str) -> None:
    job.status = JobStatus.FAILED.value
    job.error = error[:10_000]
    job.updated_at = utc_now()
    database.commit()


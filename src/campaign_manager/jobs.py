"""Transactional durable job claiming."""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.orm import Session

from campaign_manager.models import Job, JobStatus, ProcessingControl, utc_now

HEAVY_JOB_KINDS = {"transcription", "diarization", "analysis", "image_generation"}
COMPUTE_LANE_CONTROL = "__compute_lane__"


def claim_next_job(database: Session, supported_kinds: Collection[str]) -> Job | None:
    if not supported_kinds:
        return None
    paused_kinds = set(database.scalars(
        select(ProcessingControl.kind).where(ProcessingControl.paused.is_(True))
    ))
    claimable_kinds = set(supported_kinds) - paused_kinds
    if claimable_kinds & HEAVY_JOB_KINDS:
        # Serialize claim decisions across specialized workers. Once this row lock
        # is released, the newly running job is visible to every other worker.
        database.scalar(
            select(ProcessingControl)
            .where(ProcessingControl.kind == COMPUTE_LANE_CONTROL)
            .with_for_update()
        )
        heavy_running = database.scalar(
            select(Job.id).where(
                Job.status == JobStatus.RUNNING.value,
                Job.kind.in_(HEAVY_JOB_KINDS),
            ).limit(1)
        )
        if heavy_running is not None:
            claimable_kinds -= HEAVY_JOB_KINDS
    if not claimable_kinds:
        database.rollback()
        return None
    statement = (
        select(Job)
        .where(
            Job.status == JobStatus.QUEUED.value,
            Job.cancel_requested.is_(False),
            Job.kind.in_(claimable_kinds),
        )
        .order_by(Job.priority.desc(), Job.created_at, Job.id)
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
    database.refresh(job)
    job.status = (
        JobStatus.CANCELLED.value if job.cancel_requested else JobStatus.SUCCEEDED.value
    )
    job.error = None
    job.updated_at = utc_now()
    database.commit()


def fail_job(database: Session, job: Job, error: str) -> None:
    job.status = JobStatus.FAILED.value
    job.error = error[:10_000]
    job.updated_at = utc_now()
    database.commit()

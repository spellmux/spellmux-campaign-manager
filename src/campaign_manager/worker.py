"""Background worker entry point."""

from __future__ import annotations

import logging
import signal
import threading

from campaign_manager.analysis import process_analysis_job
from campaign_manager.config import Settings
from campaign_manager.database import configure_database, session_factory
from campaign_manager.diarization import process_diarization_job
from campaign_manager.enrollment import process_enrollment_job
from campaign_manager.jobs import (
    claim_next_job,
    complete_job,
    fail_job,
    recover_orphaned_jobs,
)
from campaign_manager.transcription import process_transcription_job


def main() -> None:
    settings = Settings.from_environment()
    logging.basicConfig(level=settings.log_level)
    logger = logging.getLogger("campaign_manager.worker")
    stopped = threading.Event()
    configure_database(settings.database_url)
    supported_job_kinds = {"noop"}
    if settings.transcription_provider == "faster-whisper":
        supported_job_kinds.add("transcription")
    if settings.diarization_provider == "pyannote":
        supported_job_kinds.add("diarization")
        # Enrollment needs the same embedding stack as diarization.
        supported_job_kinds.add("speaker_enrollment")
    if settings.analysis_provider == "ollama":
        supported_job_kinds.add("analysis")

    def stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    logger.info("Worker ready; supported job kinds: %s", sorted(supported_job_kinds))
    with session_factory()() as database:
        for job in recover_orphaned_jobs(database, supported_job_kinds):
            logger.warning(
                "Requeued job %s (%s) left running by a stopped worker; attempts so far: %d",
                job.id, job.kind, job.attempts,
            )
    while not stopped.is_set():
        with session_factory()() as database:
            job = claim_next_job(database, supported_job_kinds)
            if job is not None:
                try:
                    if job.kind == "transcription":
                        process_transcription_job(database, settings, job)
                    elif job.kind == "diarization":
                        process_diarization_job(database, settings, job)
                    elif job.kind == "speaker_enrollment":
                        process_enrollment_job(database, settings, job)
                    elif job.kind == "analysis":
                        process_analysis_job(database, settings, job)
                    complete_job(database, job)
                    logger.info("Completed job %s (%s)", job.id, job.kind)
                except Exception as exc:
                    logger.exception("Job %s failed", job.id)
                    fail_job(database, job, str(exc))
        stopped.wait(settings.worker_poll_seconds)
    logger.info("Worker stopped")


if __name__ == "__main__":
    main()

"""Background worker entry point.

The durable job implementation will be added with the persisted session model.
This process intentionally reports readiness without pretending to process jobs.
"""

from __future__ import annotations

import logging
import signal
import threading

from campaign_manager.config import Settings
from campaign_manager.database import configure_database, session_factory
from campaign_manager.jobs import claim_next_job, complete_job, fail_job

SUPPORTED_JOB_KINDS = {"noop"}


def main() -> None:
    settings = Settings.from_environment()
    logging.basicConfig(level=settings.log_level)
    logger = logging.getLogger("campaign_manager.worker")
    stopped = threading.Event()
    configure_database(settings.database_url)

    def stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    logger.info("Worker ready; supported job kinds: %s", sorted(SUPPORTED_JOB_KINDS))
    while not stopped.is_set():
        with session_factory()() as database:
            job = claim_next_job(database, SUPPORTED_JOB_KINDS)
            if job is not None:
                try:
                    complete_job(database, job)
                    logger.info("Completed job %s (%s)", job.id, job.kind)
                except Exception as exc:
                    logger.exception("Job %s failed", job.id)
                    fail_job(database, job, str(exc))
        stopped.wait(settings.worker_poll_seconds)
    logger.info("Worker stopped")


if __name__ == "__main__":
    main()

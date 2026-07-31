"""Background worker entry point.

The durable job implementation will be added with the persisted session model.
This process intentionally reports readiness without pretending to process jobs.
"""

from __future__ import annotations

import logging
import signal
import threading

from campaign_manager.config import Settings


def main() -> None:
    settings = Settings.from_environment()
    logging.basicConfig(level=settings.log_level)
    logger = logging.getLogger("campaign_manager.worker")
    stopped = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    logger.info("Worker ready; durable job processing is not enabled yet")
    stopped.wait()
    logger.info("Worker stopped")


if __name__ == "__main__":
    main()


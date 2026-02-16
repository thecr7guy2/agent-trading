"""Run the orchestrator scheduler daemon for autonomous trading."""

import asyncio
import logging
import signal
import sys
from pathlib import Path

from src.orchestrator.scheduler import OrchestratorScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/scheduler.log"),
    ],
)

logger = logging.getLogger(__name__)


class SchedulerDaemon:
    def __init__(self):
        self.scheduler: OrchestratorScheduler | None = None
        self.shutdown_event = asyncio.Event()

    def setup_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        def signal_handler(sig: int) -> None:
            logger.info("Received signal %s, initiating graceful shutdown...", sig)
            loop.call_soon_threadsafe(self.shutdown_event.set)

        signal.signal(signal.SIGINT, lambda s, f: signal_handler(s))
        signal.signal(signal.SIGTERM, lambda s, f: signal_handler(s))

    async def run(self) -> None:
        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)

        logger.info("Starting trading bot scheduler daemon...")
        logger.info("Timezone: Europe/Berlin")
        logger.info("Schedule:")
        logger.info("  - Reddit collection: 08:00, 12:00, 16:30 (Mon-Fri)")
        logger.info("  - Trade execution: 17:10 (Mon-Fri)")
        logger.info("  - End-of-day snapshot: 17:35 (Mon-Fri)")

        self.scheduler = OrchestratorScheduler()
        self.scheduler.start()

        logger.info("Scheduler running. Press Ctrl+C to stop.")

        try:
            await self.shutdown_event.wait()
        except Exception:
            logger.exception("Unexpected error in main loop")
        finally:
            logger.info("Shutting down scheduler...")
            if self.scheduler:
                self.scheduler.shutdown(wait=True)
            logger.info("Scheduler stopped cleanly")


def main() -> None:
    daemon = SchedulerDaemon()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    daemon.setup_signal_handlers(loop)

    try:
        loop.run_until_complete(daemon.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()

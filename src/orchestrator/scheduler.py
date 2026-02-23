import logging
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config import Settings, get_settings
from src.orchestrator.supervisor import Supervisor
from src.reporting.daily_report import generate_daily_report, write_daily_report

logger = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def _attach_daily_log_handler(run_date: date) -> logging.FileHandler:
    Path("logs").mkdir(exist_ok=True)
    handler = logging.FileHandler(f"logs/{run_date}.log")
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logging.getLogger().addHandler(handler)
    return handler


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour, minute = value.split(":", maxsplit=1)
    return int(hour), int(minute)


class OrchestratorScheduler:
    def __init__(self, supervisor: Supervisor | None = None, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._supervisor = supervisor or Supervisor(settings=self._settings)
        timezone = ZoneInfo(self._settings.orchestrator_timezone)
        self._scheduler = AsyncIOScheduler(
            timezone=timezone,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 300,
            },
        )
        self._last_decision_result: dict | None = None

    async def _run_decision_job(self) -> None:
        run_date = date.today()
        handler = _attach_daily_log_handler(run_date)
        try:
            result = await self._supervisor.run_decision_cycle()
            logger.info("Decision cycle finished: %s", result)
            if result.get("status") == "ok":
                self._last_decision_result = result
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()

    async def _run_eod_job(self) -> None:
        run_date = date.today()
        handler = _attach_daily_log_handler(run_date)
        try:
            result = await self._supervisor.run_end_of_day()
            logger.info("End-of-day snapshot finished: %s", result)

            decision_result = self._last_decision_result or {}
            if result.get("status") == "ok":
                eod_date = date.fromisoformat(result["date"])
                try:
                    report = await generate_daily_report(
                        run_date=eod_date,
                        decision_result=decision_result,
                        eod_result=result,
                    )
                    path = write_daily_report(report, eod_date)
                    logger.info("Daily report written to %s", path)
                except Exception:
                    logger.exception("Failed to generate daily report")

            self._last_decision_result = None
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()

    def configure_jobs(self) -> None:
        if self._scheduler.get_jobs():
            return

        # Trade execution — Tuesday and Friday only (busiest filing days)
        decision_hour, decision_minute = _parse_hhmm(self._settings.scheduler_execute_time)
        self._scheduler.add_job(
            self._run_decision_job,
            trigger="cron",
            id="decision_and_execution",
            day_of_week=self._settings.scheduler_trade_days,
            hour=decision_hour,
            minute=decision_minute,
            replace_existing=True,
        )

        # EOD snapshot + report — same days as trade execution
        eod_hour, eod_minute = _parse_hhmm(self._settings.scheduler_eod_time)
        self._scheduler.add_job(
            self._run_eod_job,
            trigger="cron",
            id="end_of_day_snapshot",
            day_of_week=self._settings.scheduler_trade_days,
            hour=eod_hour,
            minute=eod_minute,
            replace_existing=True,
        )

    def start(self) -> None:
        self.configure_jobs()
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("Orchestrator scheduler started")

    def shutdown(self, wait: bool = False) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)
            logger.info("Orchestrator scheduler stopped")

    @property
    def scheduler(self) -> AsyncIOScheduler:
        return self._scheduler

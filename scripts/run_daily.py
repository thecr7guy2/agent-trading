"""Manual trigger for the daily trading pipeline."""

import argparse
import asyncio
import json
import logging
from datetime import date
from pathlib import Path

from src.orchestrator.supervisor import Supervisor
from src.reporting.daily_report import generate_daily_report, write_daily_report

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a single decision cycle")
    parser.add_argument("--date", dest="run_date", help="Run date in YYYY-MM-DD format")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass weekday check and N-day frequency guard (useful for testing on weekends)",
    )
    parser.add_argument(
        "--skip-eod",
        action="store_true",
        help="Skip end-of-day snapshot and daily report generation",
    )
    return parser


async def _run(args: argparse.Namespace) -> dict:
    run_date = date.fromisoformat(args.run_date) if args.run_date else date.today()

    Path("logs").mkdir(exist_ok=True)
    handler = logging.FileHandler(f"logs/{run_date}.log")
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logging.getLogger().addHandler(handler)

    try:
        supervisor = Supervisor()

        decision_result = await supervisor.run_decision_cycle(
            run_date=run_date if args.run_date else None,
            force=args.force,
        )

        if decision_result.get("status") != "ok" or args.skip_eod:
            return decision_result

        actual_date = date.fromisoformat(decision_result["date"])
        logger.info("Waiting 30s for T212 orders to settle before EOD snapshot...")
        await asyncio.sleep(30)
        eod_result = await supervisor.run_end_of_day(actual_date)

        report_content = await generate_daily_report(
            run_date=actual_date,
            decision_result=decision_result,
            eod_result=eod_result,
        )
        report_path = write_daily_report(report_content, actual_date)
        logger.info("Daily report written to %s", report_path)

        try:
            from src.reporting.dashboard import push_dashboard_data, update_dashboard_data
            update_dashboard_data(decision_result=decision_result, eod_result=eod_result)
            push_dashboard_data()
        except Exception:
            logger.exception("Failed to update dashboard")

        decision_result["eod"] = eod_result
        decision_result["report_path"] = str(report_path)
        return decision_result
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    result = asyncio.run(_run(args))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

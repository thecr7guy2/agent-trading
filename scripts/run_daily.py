"""Manual trigger for the daily trading pipeline."""

import argparse
import asyncio
import json
import logging
from datetime import date

from src.orchestrator.supervisor import Supervisor
from src.reporting.daily_report import generate_daily_report, write_daily_report

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a single decision cycle")
    parser.add_argument("--date", dest="run_date", help="Run date in YYYY-MM-DD format")
    parser.add_argument(
        "--no-approval",
        action="store_true",
        help="Skip CLI approval and auto-approve all picks",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore duplicate-trade guard and execute anyway",
    )
    parser.add_argument(
        "--collect-rounds",
        type=int,
        default=0,
        help="If > 0, trigger a single Reddit RSS collection round before running pipeline",
    )
    parser.add_argument(
        "--skip-eod",
        action="store_true",
        help="Skip end-of-day snapshot and daily report generation",
    )
    return parser


async def _run(args: argparse.Namespace) -> dict:
    run_date = date.fromisoformat(args.run_date) if args.run_date else None
    supervisor = Supervisor()

    decision_result = await supervisor.run_decision_cycle(
        run_date=run_date,
        require_approval=not args.no_approval,
        force=args.force,
        collect_rounds=args.collect_rounds,
    )

    if decision_result.get("status") != "ok" or args.skip_eod:
        return decision_result

    actual_date = date.fromisoformat(decision_result["date"])
    eod_result = await supervisor.run_end_of_day(actual_date)

    report_content = await generate_daily_report(
        run_date=actual_date,
        decision_result=decision_result,
        eod_result=eod_result,
    )
    report_path = write_daily_report(report_content, actual_date)
    logger.info("Daily report written to %s", report_path)

    decision_result["eod"] = eod_result
    decision_result["report_path"] = str(report_path)
    return decision_result


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    result = asyncio.run(_run(args))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

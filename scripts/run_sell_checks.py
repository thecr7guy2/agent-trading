"""Manually trigger sell strategy checks."""

import argparse
import asyncio
import json
from datetime import date

from src.orchestrator.supervisor import Supervisor


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run sell strategy checks")
    parser.add_argument("--date", dest="run_date", help="Run date in YYYY-MM-DD format")
    parser.add_argument("--real-only", action="store_true", help="Only check real positions")
    parser.add_argument("--virtual-only", action="store_true", help="Only check virtual positions")
    return parser


async def _run(args: argparse.Namespace) -> dict:
    run_date = date.fromisoformat(args.run_date) if args.run_date else None
    supervisor = Supervisor()

    include_real = not args.virtual_only
    include_virtual = not args.real_only

    return await supervisor.run_sell_checks(
        run_date=run_date,
        include_real=include_real,
        include_virtual=include_virtual,
    )


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    result = asyncio.run(_run(args))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

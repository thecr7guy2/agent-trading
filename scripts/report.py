"""Generate P&L and leaderboard reports."""

import argparse
import asyncio
from datetime import date, timedelta

from src.db.connection import get_pool
from src.mcp_servers.trading.portfolio import PortfolioManager
from src.orchestrator.mcp_client import create_market_data_client
from src.reporting.formatter import print_report
from src.reporting.leaderboard import LeaderboardBuilder
from src.reporting.pnl import PnLEngine


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a trading report")
    parser.add_argument(
        "--period",
        required=True,
        choices=["day", "week", "month", "all"],
        help="Report period",
    )
    parser.add_argument(
        "--date",
        dest="end_date",
        help="End date in YYYY-MM-DD format (defaults to today)",
    )
    return parser


def _compute_date_range(period: str, end_date: date) -> tuple[date, date]:
    if period == "day":
        return end_date, end_date
    if period == "week":
        start = end_date - timedelta(days=end_date.weekday())
        return start, end_date
    if period == "month":
        start = end_date.replace(day=1)
        return start, end_date
    # "all" — go back ~1 year
    return end_date - timedelta(days=365), end_date


def _period_label(period: str, start: date, end: date) -> str:
    if period == "day":
        return f"DAILY REPORT ({end})"
    if period == "week":
        return f"WEEKLY REPORT ({start} – {end})"
    if period == "month":
        return f"MONTHLY REPORT ({start.strftime('%B %Y')})"
    return f"ALL-TIME REPORT (through {end})"


async def _run(args: argparse.Namespace) -> None:
    end = date.fromisoformat(args.end_date) if args.end_date else date.today()
    start, end = _compute_date_range(args.period, end)
    label = _period_label(args.period, start, end)

    pool = await get_pool()
    pm = PortfolioManager(pool)
    market_client = create_market_data_client()

    engine = PnLEngine(pm, market_client)
    builder = LeaderboardBuilder(engine)

    leaderboard = await builder.build(start, end)
    summary = await engine.get_portfolio_summary(is_real=True)
    best_worst = await engine.get_best_worst_picks(start, end)

    print_report(label, leaderboard, summary, best_worst)

    await market_client.close()


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()

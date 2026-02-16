"""Run a historical backtest over a date range."""

import argparse
import asyncio
import json
import logging
from datetime import date
from pathlib import Path

from src.backtesting.engine import BacktestEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run historical backtest")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--name", help="Name for this backtest run")
    parser.add_argument("--budget", type=float, help="Daily budget in EUR (default from config)")
    return parser


def _generate_report(result) -> str:
    lines = [
        f"# Backtest Report: {result.name}",
        "",
        f"- **Period:** {result.start_date} to {result.end_date}",
        f"- **Trading days:** {result.days_traded}",
        f"- **Run ID:** {result.run_id}",
        "",
        "## Results by Portfolio",
        "",
        "| Portfolio | Invested | Realized P&L | Trades | Wins | Losses | Win Rate |",
        "|-----------|----------|-------------|--------|------|--------|----------|",
    ]

    for key, data in sorted(result.llm_results.items()):
        total_trades = data["wins"] + data["losses"]
        win_rate = f"{data['wins'] / total_trades * 100:.1f}%" if total_trades > 0 else "N/A"
        pnl = data["realized_pnl"]
        pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"

        lines.append(
            f"| {key} | {data['total_invested']:.2f} | {pnl_str} | "
            f"{data['total_trades']} | {data['wins']} | {data['losses']} | {win_rate} |"
        )

    lines.append("")

    # Determine winner
    real_results = {k: v for k, v in result.llm_results.items() if k.endswith("_real")}
    if real_results:
        winner = max(real_results, key=lambda k: real_results[k]["realized_pnl"])
        lines.append(f"**Winner (real):** {winner} with {real_results[winner]['realized_pnl']:+.2f} EUR")
        lines.append("")

    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> None:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    engine = BacktestEngine()
    result = await engine.run(
        start_date=start,
        end_date=end,
        run_name=args.name,
        budget_eur=args.budget,
    )

    # Print terminal summary
    print(f"\nBacktest complete: {result.name}")
    print(f"Period: {result.start_date} to {result.end_date}")
    print(f"Days traded: {result.days_traded}")
    print()

    for key, data in sorted(result.llm_results.items()):
        total_trades = data["wins"] + data["losses"]
        win_rate = f"{data['wins'] / total_trades * 100:.1f}%" if total_trades > 0 else "N/A"
        pnl = data["realized_pnl"]
        pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
        print(f"  {key}: P&L={pnl_str}, Trades={data['total_trades']}, Win rate={win_rate}")

    # Write markdown report
    report_dir = Path("reports/backtests")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_name = result.name or f"backtest_{start}_{end}"
    report_path = report_dir / f"{report_name}.md"
    report_content = _generate_report(result)
    report_path.write_text(report_content, encoding="utf-8")
    print(f"\nReport written to {report_path}")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()

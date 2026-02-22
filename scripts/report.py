"""Generate a portfolio P&L report from current T212 positions."""

import argparse
import asyncio
from decimal import Decimal

from src.config import get_settings
from src.mcp_servers.trading.portfolio import get_demo_positions, get_live_positions
from src.mcp_servers.trading.t212_client import T212Client


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show current portfolio positions and P&L")
    parser.add_argument(
        "--account",
        choices=["live", "demo", "both"],
        default="both",
        help="Which account to show (default: both)",
    )
    return parser


def _format_positions(positions: list[dict], label: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")

    if not positions:
        print("  No open positions.")
        return

    total_invested = Decimal("0")
    total_value = Decimal("0")

    print(f"  {'Ticker':<10} {'Qty':>8} {'Avg':>10} {'Price':>10} {'Value':>10} {'P&L':>10} {'%':>7}")
    print(f"  {'-' * 65}")

    for pos in sorted(positions, key=lambda p: p.get("ticker", "")):
        ticker = pos.get("ticker", "?")
        qty = Decimal(str(pos.get("quantity", 0)))
        avg = Decimal(str(pos.get("avg_buy_price", 0)))
        price = Decimal(str(pos.get("current_price", 0)))
        invested = qty * avg
        value = qty * price if price > 0 else invested
        pnl = value - invested
        pnl_pct = float(pnl / invested * 100) if invested > 0 else 0.0

        total_invested += invested
        total_value += value

        pnl_sign = "+" if pnl >= 0 else ""
        pnl_pct_sign = "+" if pnl_pct >= 0 else ""
        print(
            f"  {ticker:<10} {float(qty):>8.4f} {float(avg):>10.2f} {float(price):>10.2f} "
            f"{float(value):>10.2f} {pnl_sign}{float(pnl):>9.2f} {pnl_pct_sign}{pnl_pct:>6.1f}%"
        )

    print(f"  {'-' * 65}")
    total_pnl = total_value - total_invested
    total_pnl_pct = float(total_pnl / total_invested * 100) if total_invested > 0 else 0.0
    pnl_sign = "+" if total_pnl >= 0 else ""
    pnl_pct_sign = "+" if total_pnl_pct >= 0 else ""
    print(
        f"  {'TOTAL':<10} {'':>8} {'':>10} {'':>10} "
        f"{float(total_value):>10.2f} {pnl_sign}{float(total_pnl):>9.2f} "
        f"{pnl_pct_sign}{total_pnl_pct:>6.1f}%"
    )
    print(f"  Invested: €{float(total_invested):.2f}")


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()

    if args.account in ("live", "both"):
        try:
            t212_live = T212Client(
                api_key=settings.t212_api_key,
                api_secret=settings.t212_api_secret,
                use_demo=False,
            )
            live_positions = await get_live_positions(t212_live)
            _format_positions(live_positions, "LIVE ACCOUNT (Real Money)")
        except Exception as e:
            print(f"\nFailed to fetch live positions: {e}")

    if args.account in ("demo", "both"):
        try:
            t212_demo = T212Client(
                api_key=settings.t212_api_key,
                api_secret=settings.t212_api_secret or "",
                use_demo=True,
            )
            demo_positions = await get_demo_positions(t212_demo)
            _format_positions(demo_positions, "DEMO ACCOUNT (Practice)")
        except Exception as e:
            print(f"\nFailed to fetch demo positions: {e}")

    print()


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()

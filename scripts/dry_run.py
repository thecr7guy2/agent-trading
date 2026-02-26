"""
Dry-run the full pipeline (digest → research → decision) without executing any T212 trades.
Prints the picks Claude selects with EUR amounts per position.

Usage:
    uv run python scripts/dry_run.py
    uv run python scripts/dry_run.py --budget 1500
    uv run python scripts/dry_run.py --lookback 7
"""

import argparse
import asyncio
import logging

from rich import box
from rich.console import Console
from rich.table import Table

from src.agents.pipeline import AgentPipeline
from src.config import get_settings
from src.utils.recently_traded import get_blacklist

console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
# Quiet noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run the trading pipeline (no trades placed)")
    parser.add_argument("--budget", type=float, help="Budget in EUR (overrides .env)")
    parser.add_argument("--lookback", type=int, help="Insider lookback days (overrides .env)")
    return parser


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    if args.budget:
        settings.budget_per_run_eur = args.budget
    if args.lookback:
        settings.insider_lookback_days = args.lookback
        settings.capitol_trades_lookback_days = args.lookback

    budget = settings.budget_per_run_eur

    console.rule("[bold cyan]Dry Run — no trades will be placed[/bold cyan]")

    # --- Step 1: Build digest (OpenInsider + Capitol Trades) ---
    console.print("\n[bold]Step 1/2[/bold] — Fetching insider + politician candidates…")
    from src.orchestrator.supervisor import Supervisor

    supervisor = Supervisor(settings=settings)
    digest = await supervisor.build_insider_digest()
    total_candidates = digest.get("insider_count", 0)
    source_counts = digest.get("source_counts", {})
    console.print(
        f"  Found [bold]{total_candidates}[/bold] candidates "
        f"(OpenInsider: {source_counts.get('openinsider', 0)}, "
        f"Capitol Trades: {source_counts.get('capitol_trades', 0)})"
    )

    if total_candidates == 0:
        console.print("[red]No candidates found — nothing to pick from. Exiting.[/red]")
        return

    # --- Step 2: Filter blacklist + cap for research stage ---
    blacklist = get_blacklist(
        path=settings.recently_traded_path,
        days=settings.recently_traded_days,
    )
    all_candidates = digest.get("candidates", [])
    blacklisted = [c["ticker"] for c in all_candidates if c["ticker"] in blacklist]
    filtered = [c for c in all_candidates if c["ticker"] not in blacklist]

    if blacklisted:
        console.print(f"  [yellow]Blacklisted (recently traded):[/yellow] {', '.join(blacklisted)}")

    if settings.capitol_trades_enabled:
        insider_pool = [c for c in filtered if c.get("source") != "capitol_trades"]
        politician_pool = [c for c in filtered if c.get("source") == "capitol_trades"]
        reserved = settings.capitol_trades_reserved_slots
        politician_slots = min(len(politician_pool), reserved)
        insider_slots = settings.research_top_n - politician_slots
        capped = politician_pool[:politician_slots] + insider_pool[:insider_slots]
    else:
        capped = filtered[: settings.research_top_n]

    digest["candidates"] = capped
    console.print(f"  Passing [bold]{len(capped)}[/bold] candidates to research stage")

    # --- Step 3: Decision (Claude Opus, no research pre-pass) ---
    console.print(f"\n[bold]Step 2/2[/bold] — Decision stage (Claude Opus, budget €{budget:,.0f})…")
    pipeline = AgentPipeline()
    output = await pipeline.run(
        enriched_digest=digest,
        portfolio=[],  # no existing positions for dry run
        budget_eur=budget,
    )

    picks = [p for p in output.picks.picks if p.action == "buy"]
    picks = sorted(picks, key=lambda p: p.allocation_pct, reverse=True)

    # --- Output ---
    console.print()
    console.rule("[bold green]Claude's Picks[/bold green]")
    console.print(
        f"  Overall confidence: [bold]{output.picks.confidence:.0%}[/bold]   "
        f"Budget: [bold]€{budget:,.0f}[/bold]\n"
    )

    if output.picks.market_summary:
        console.print(f"[dim]{output.picks.market_summary}[/dim]\n")

    if not picks:
        console.print("[yellow]Claude made no buy picks for this run.[/yellow]")
        return

    table = Table(box=box.ROUNDED, show_lines=True, highlight=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Ticker", style="bold cyan", width=10)
    table.add_column("Source", style="magenta", width=24)
    table.add_column("Alloc %", justify="right", width=8)
    table.add_column("EUR", justify="right", style="bold green", width=10)
    table.add_column("Reasoning", width=60)

    total_allocated = 0.0
    for i, pick in enumerate(picks, 1):
        eur = budget * pick.allocation_pct / 100
        total_allocated += pick.allocation_pct

        # Resolve source from digest candidates
        candidate = next(
            (c for c in capped if c["ticker"] == pick.ticker),
            None,
        )
        source = (candidate or {}).get("source", pick.source or "—")
        source_label = {
            "openinsider": "OpenInsider",
            "capitol_trades": "Capitol Trades",
            "openinsider+capitol_trades": "OpenInsider + Capitol Trades",
        }.get(source, source)

        # Truncate reasoning for table display
        reasoning = pick.reasoning
        if len(reasoning) > 200:
            reasoning = reasoning[:197] + "…"

        table.add_row(
            str(i),
            pick.ticker,
            source_label,
            f"{pick.allocation_pct:.1f}%",
            f"€{eur:,.0f}",
            reasoning,
        )

    console.print(table)
    console.print(
        f"\n  Total allocated: [bold]€{budget * total_allocated / 100:,.0f}[/bold] "
        f"({total_allocated:.1f}% of €{budget:,.0f} budget)"
    )

    # Sell recommendations (if any)
    sells = [p for p in output.picks.picks if p.action == "sell"]
    if sells:
        console.print("\n[bold yellow]Sell Recommendations[/bold yellow]")
        for p in sells:
            console.print(f"  [yellow]SELL {p.ticker}[/yellow] — {p.reasoning}")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()

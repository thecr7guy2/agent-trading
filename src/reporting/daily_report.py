import re
from datetime import date
from pathlib import Path

from src.config import get_settings


def _fmt_eur(value: float | str) -> str:
    try:
        return f"€{float(value):.2f}"
    except (TypeError, ValueError):
        return "€—"


def _fmt_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f}%"


def _days_held(pos: dict) -> str:
    open_date = pos.get("open_date") or pos.get("opened_at", "")
    if not open_date:
        return "—"
    match = re.match(r"(\d{4}-\d{2}-\d{2})", str(open_date))
    if not match:
        return "—"
    try:
        opened = date.fromisoformat(match.group(1))
        return str((date.today() - opened).days)
    except ValueError:
        return "—"


def _position_return(pos: dict) -> float:
    try:
        avg = float(pos.get("avg_buy_price", 0) or 0)
        current = float(pos.get("current_price", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if avg <= 0 or current <= 0:
        return 0.0
    return (current - avg) / avg * 100


async def generate_daily_report(
    run_date: date,
    decision_result: dict,
    eod_result: dict,
) -> str:
    settings = get_settings()
    day_name = run_date.strftime("%A")
    lines = [f"# Trading Report — {run_date} ({day_name})", ""]

    status = decision_result.get("status", "skipped")
    execution = decision_result.get("execution", [])
    picks = decision_result.get("picks", [])
    insider_count = decision_result.get("insider_count", 0)
    blacklisted = decision_result.get("blacklisted", [])
    confidence = decision_result.get("confidence", 0.0)
    market_summary = decision_result.get("market_summary", "")

    # Build ticker→reasoning lookup from picks
    reasoning_map: dict[str, str] = {
        p["ticker"]: p.get("reasoning", "") for p in picks if p.get("ticker")
    }

    bought = [t for t in execution if t.get("status") == "filled"]
    failed = [t for t in execution if t.get("status") != "filled"]
    total_spent = sum(float(t.get("amount_eur", 0) or 0) for t in bought)

    # --- Summary ---
    lines.append("## Summary")
    if status == "skipped":
        reason = decision_result.get("reason", "no reason given")
        lines.append(f"- **No trades today** — {reason}")
    elif status == "error":
        error = decision_result.get("error", "unknown error")
        lines.append(f"- **Pipeline error** — {error}")
    else:
        lines.append(
            f"- Spent {_fmt_eur(total_spent)} / {_fmt_eur(settings.budget_per_run_eur)}"
            f" — {len(bought)} stock{'s' if len(bought) != 1 else ''} bought"
        )
        lines.append(f"- Insider candidates today: {insider_count}")
        if confidence:
            lines.append(f"- Claude confidence: {confidence:.0%}")
        if market_summary:
            lines.append(f"- Market context: {market_summary}")
    lines.append("")

    # --- Today's Buys ---
    lines.append("## Today's Buys")
    lines.append("")
    if not bought:
        lines.append("_No positions taken today._")
        lines.append("")
    else:
        lines.append("| Ticker | Amount | Qty | Why Claude bought it |")
        lines.append("|--------|--------|-----|----------------------|")
        for trade in bought:
            ticker = trade.get("ticker", "?")
            amount_eur = float(trade.get("amount_eur", 0) or 0)
            qty = float(trade.get("quantity", 0) or 0)
            reasoning = (reasoning_map.get(ticker) or "—").replace("|", "/")
            if len(reasoning) > 120:
                reasoning = reasoning[:117] + "..."
            lines.append(
                f"| {ticker} | {_fmt_eur(amount_eur)} | {qty:.3f} | {reasoning} |"
            )
        lines.append("")

    # --- Skipped / Failed ---
    skipped_rows: list[tuple[str, str]] = []
    seen: set[str] = set()

    for ticker in blacklisted:
        if ticker not in seen:
            seen.add(ticker)
            skipped_rows.append(
                (ticker, f"Blacklisted — bought within {settings.recently_traded_days} days")
            )

    for trade in failed:
        ticker = trade.get("ticker", "?")
        if ticker not in seen:
            seen.add(ticker)
            error = (trade.get("error") or "unknown error").replace("|", "/")
            skipped_rows.append((ticker, error))

    if skipped_rows:
        lines.append("## Skipped / Failed")
        lines.append("| Ticker | Reason |")
        lines.append("|--------|--------|")
        for ticker, reason in skipped_rows:
            lines.append(f"| {ticker} | {reason} |")
        lines.append("")

    # --- Current Positions ---
    demo_positions = eod_result.get("demo_positions", [])
    if demo_positions:
        lines.append("## Current Positions (Live from T212)")
        lines.append("")
        lines.append("| Ticker | Bought at | Now | P&L | Days held |")
        lines.append("|--------|-----------|-----|-----|-----------|")
        for pos in sorted(demo_positions, key=lambda p: p.get("ticker", "")):
            ticker = pos.get("ticker", "?")
            try:
                avg = float(pos.get("avg_buy_price", 0) or 0)
                current = float(pos.get("current_price", 0) or 0)
            except (TypeError, ValueError):
                avg, current = 0.0, 0.0
            return_pct = _position_return(pos)
            days = _days_held(pos)
            avg_str = _fmt_eur(avg) if avg > 0 else "—"
            current_str = _fmt_eur(current) if current > 0 else "—"
            pnl_str = _fmt_pct(return_pct) if avg > 0 and current > 0 else "—"
            lines.append(f"| {ticker} | {avg_str} | {current_str} | {pnl_str} | {days} |")
        lines.append("")

        # Portfolio snapshot
        snapshot = eod_result.get("snapshots", {}).get("demo", {})
        if snapshot:
            lines.append(
                f"**Portfolio:** invested {_fmt_eur(snapshot.get('total_invested', 0))}"
                f" | value {_fmt_eur(snapshot.get('total_value', 0))}"
                f" | unrealised P&L {_fmt_eur(snapshot.get('unrealized_pnl', 0))}"
            )
            lines.append("")

    return "\n".join(lines)


def write_daily_report(content: str, run_date: date, reports_dir: str = "reports") -> Path:
    directory = Path(reports_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_path = directory / f"{run_date}.md"
    file_path.write_text(content, encoding="utf-8")
    return file_path

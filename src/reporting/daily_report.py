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


def _signal_label(candidate: dict | None) -> str:
    if not candidate:
        return "—"
    sources = candidate.get("sources", [])
    parts = []
    if "insider" in sources:
        parts.append("Insider buy")
    if "earnings" in sources:
        parts.append("Earnings")
    if "screener" in sources:
        parts.append("Screener")
    if "reddit" in sources:
        parts.append("Reddit")
    return ", ".join(parts) if parts else "—"


def _company_name(candidate: dict | None, ticker: str) -> str:
    if not candidate:
        return ticker
    insider = candidate.get("insider", {})
    if insider.get("company"):
        return insider["company"]
    screener = candidate.get("screener", {})
    return screener.get("name") or screener.get("company") or ticker


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
    avg = float(pos.get("avg_buy_price", 0) or 0)
    current = float(pos.get("current_price", 0) or 0)
    if avg <= 0 or current <= 0:
        return 0.0
    return (current - avg) / avg * 100


async def generate_daily_report(
    run_date: date,
    decision_result: dict,
    eod_result: dict,
    sell_results: list[dict] | None = None,
) -> str:
    settings = get_settings()
    day_name = run_date.strftime("%A")
    lines = [f"# Trading Report — {run_date} ({day_name})", ""]

    # Build ticker→candidate lookup for enrichment
    signal_digest = decision_result.get("signal_digest", {})
    candidate_map: dict[str, dict] = {
        c["ticker"]: c
        for c in signal_digest.get("candidates", [])
        if c.get("ticker")
    }

    # Build ticker→reasoning lookup from pipeline analysis
    reasoning_map: dict[str, str] = {}
    for strategy_key in ("conservative", "aggressive"):
        analysis = decision_result.get("pipeline_analysis", {}).get(strategy_key, {})
        for pick in analysis.get("picks", []):
            ticker = pick.get("ticker", "")
            if ticker and ticker not in reasoning_map:
                reasoning_map[ticker] = pick.get("reasoning", "")

    real_exec = decision_result.get("real_execution", [])
    practice_exec = decision_result.get("practice_execution", [])
    real_bought = [t for t in real_exec if t.get("status") == "filled"]
    practice_bought = [t for t in practice_exec if t.get("status") == "filled"]
    real_spent = sum(float(t.get("amount_eur", 0) or 0) for t in real_bought)
    practice_spent = sum(float(t.get("amount_eur", 0) or 0) for t in practice_bought)

    # --- Summary ---
    lines.append("## Summary")
    lines.append(
        f"- Conservative (Real): spent {_fmt_eur(real_spent)} / {_fmt_eur(settings.daily_budget_eur)}"
        f" — {len(real_bought)} stock{'s' if len(real_bought) != 1 else ''} bought"
    )
    lines.append(
        f"- Practice (Demo): spent {_fmt_eur(practice_spent)} / {_fmt_eur(settings.practice_daily_budget_eur)}"
        f" — {len(practice_bought)} stock{'s' if len(practice_bought) != 1 else ''} bought"
    )
    lines.append("")

    # --- Today's Buys ---
    lines.append("## Today's Buys")
    lines.append("")

    def _buys_table(exec_list: list[dict], section_title: str) -> list[str]:
        bought = [t for t in exec_list if t.get("status") == "filled"]
        section = [f"### {section_title}"]
        if not bought:
            section.append("_No positions taken today._")
            section.append("")
            return section
        section.append("| Ticker | Company | Amount | Price | Signal Sources | Why Claude bought it |")
        section.append("|--------|---------|--------|-------|----------------|----------------------|")
        for trade in bought:
            ticker = trade.get("ticker", "?")
            candidate = candidate_map.get(ticker)
            company = _company_name(candidate, ticker).replace("|", "/")
            amount_eur = float(trade.get("amount_eur", 0) or 0)
            qty = float(trade.get("quantity", 0) or 0)
            price = amount_eur / qty if qty > 0 else 0.0
            signals = _signal_label(candidate)
            reasoning = (reasoning_map.get(ticker) or "—").replace("|", "/")
            if len(reasoning) > 100:
                reasoning = reasoning[:97] + "..."
            price_str = _fmt_eur(price) if price > 0 else "—"
            section.append(
                f"| {ticker} | {company} | {_fmt_eur(amount_eur)} | {price_str}"
                f" | {signals} | {reasoning} |"
            )
        section.append("")
        return section

    lines.extend(_buys_table(real_exec, "Conservative — Real Money"))
    if practice_exec:
        lines.extend(_buys_table(practice_exec, "Practice — Demo Account"))

    # --- Skipped / Failed ---
    all_failed = [t for t in real_exec + practice_exec if t.get("status") != "filled"]
    blacklisted = decision_result.get("blacklisted_candidates", [])
    conservative_analysis = decision_result.get("pipeline_analysis", {}).get("conservative", {})
    not_picked = conservative_analysis.get("not_picked", [])

    skipped_rows: list[tuple[str, str]] = []
    seen: set[str] = set()

    for ticker in blacklisted:
        if ticker not in seen:
            seen.add(ticker)
            skipped_rows.append((ticker, f"Blacklisted — bought within {settings.recently_traded_days} days"))

    for trade in all_failed:
        ticker = trade.get("ticker", "?")
        if ticker not in seen:
            seen.add(ticker)
            error = (trade.get("error") or "unknown error").replace("|", "/")
            skipped_rows.append((ticker, error))

    for r in not_picked[:5]:
        ticker = r.get("ticker", "?")
        if ticker not in seen:
            seen.add(ticker)
            reasons = []
            if r.get("risk_score", 0) >= 7:
                reasons.append("high risk")
            if r.get("fundamental_score", 0) < 5:
                reasons.append("weak fundamentals")
            if r.get("technical_score", 0) < 5:
                reasons.append("weak technicals")
            if not reasons:
                reasons.append("lower conviction")
            skipped_rows.append((ticker, f"Claude: {', '.join(reasons)}"))

    if skipped_rows:
        lines.append("## Skipped / Failed")
        lines.append("| Ticker | Reason |")
        lines.append("|--------|--------|")
        for ticker, reason in skipped_rows:
            lines.append(f"| {ticker} | {reason} |")
        lines.append("")

    # --- Current Positions ---
    live_positions = eod_result.get("live_positions", [])
    demo_positions = eod_result.get("demo_positions", [])

    if live_positions or demo_positions:
        lines.append("## Current Positions (Live from T212)")
        lines.append("")

        def _positions_table(positions: list[dict], section_title: str) -> list[str]:
            section = [f"### {section_title}"]
            if not positions:
                section.append("_No open positions._")
                section.append("")
                return section
            section.append("| Ticker | Bought at | Now | P&L | Days held |")
            section.append("|--------|-----------|-----|-----|-----------|")
            for pos in sorted(positions, key=lambda p: p.get("ticker", "")):
                ticker = pos.get("ticker", "?")
                avg = float(pos.get("avg_buy_price", 0) or 0)
                current = float(pos.get("current_price", 0) or 0)
                return_pct = _position_return(pos)
                days = _days_held(pos)
                avg_str = _fmt_eur(avg) if avg > 0 else "—"
                current_str = _fmt_eur(current) if current > 0 else "—"
                pnl_str = _fmt_pct(return_pct) if avg > 0 and current > 0 else "—"
                section.append(f"| {ticker} | {avg_str} | {current_str} | {pnl_str} | {days} |")
            section.append("")
            return section

        lines.extend(_positions_table(live_positions, "Real Account"))
        if demo_positions:
            lines.extend(_positions_table(demo_positions, "Practice Account"))

    # --- Sell Triggers ---
    if sell_results:
        lines.append("## Sell Triggers")
        lines.append("| Ticker | Type | Return | Reason |")
        lines.append("|--------|------|--------|--------|")
        for sell in sell_results:
            ticker = sell.get("ticker", "?")
            signal_type = sell.get("signal_type", "?")
            return_pct = float(sell.get("return_pct", 0))
            reasoning = (sell.get("reasoning") or "").replace("|", "/")
            lines.append(f"| {ticker} | {signal_type} | {_fmt_pct(return_pct)} | {reasoning} |")
        lines.append("")

    return "\n".join(lines)


def write_daily_report(content: str, run_date: date, reports_dir: str = "reports") -> Path:
    directory = Path(reports_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_path = directory / f"{run_date}.md"
    file_path.write_text(content, encoding="utf-8")
    return file_path

from datetime import date
from pathlib import Path


async def generate_daily_report(
    run_date: date,
    decision_result: dict,
    eod_result: dict,
) -> str:
    day_name = run_date.strftime("%A")
    lines = [f"# Daily Trading Report — {run_date} ({day_name})", ""]

    main_trader = decision_result.get("main_trader", "unknown")
    virtual_trader = decision_result.get("virtual_trader", "unknown")

    # Roles
    lines.append("## Roles")
    lines.append(f"- **Main Trader:** {main_trader.capitalize()} (real money)")
    lines.append(f"- **Virtual Trader:** {virtual_trader.capitalize()} (paper trades)")
    lines.append("")

    # Reddit Digest
    reddit_posts = decision_result.get("reddit_posts", 0)
    tickers_analyzed = decision_result.get("tickers_analyzed", 0)
    lines.append("## Reddit Digest")
    lines.append(f"- Posts analyzed: {reddit_posts}")
    lines.append(f"- Tickers evaluated: {tickers_analyzed}")
    lines.append("")

    # Picks & Execution
    lines.append("## Picks & Execution")

    # Main trader
    real_exec = decision_result.get("real_execution", [])
    lines.append(f"### {main_trader.capitalize()} (Main — Real Trades)")
    lines.append("| Ticker | Action | Status |")
    lines.append("|--------|--------|--------|")
    if real_exec:
        for trade in real_exec:
            ticker = trade.get("ticker", "?")
            action = trade.get("action", "buy")
            status = trade.get("status", "unknown")
            lines.append(f"| {ticker} | {action} | {status} |")
    else:
        lines.append("| — | — | no trades |")
    lines.append("")

    # Virtual trader
    virtual_exec = decision_result.get("virtual_execution", [])
    lines.append(f"### {virtual_trader.capitalize()} (Virtual)")
    lines.append("| Ticker | Action | Status |")
    lines.append("|--------|--------|--------|")
    if virtual_exec:
        for trade in virtual_exec:
            ticker = trade.get("ticker", "?")
            action = trade.get("action", "buy")
            status = trade.get("status", "unknown")
            lines.append(f"| {ticker} | {action} | {status} |")
    else:
        lines.append("| — | — | no trades |")
    lines.append("")

    # Portfolio Snapshot
    snapshots = eod_result.get("snapshots", {})
    if snapshots:
        lines.append("## Portfolio Snapshot")
        lines.append("| Portfolio | Invested | Value | Unrealized P&L |")
        lines.append("|-----------|----------|-------|----------------|")
        for label, snap in snapshots.items():
            invested = snap.get("total_invested", "0")
            value = snap.get("total_value", "0")
            unrealized = snap.get("unrealized_pnl", "0")
            lines.append(f"| {label} | {invested} | {value} | {unrealized} |")
        lines.append("")

    # Summary
    approval = decision_result.get("approval", {})
    lines.append("## Summary")
    lines.append(f"- Approval: {approval.get('action', 'unknown')}")
    lines.append(f"- Real trades executed: {len(real_exec)}")
    lines.append(f"- Virtual trades executed: {len(virtual_exec)}")
    lines.append("")

    return "\n".join(lines)


def write_daily_report(content: str, run_date: date, reports_dir: str = "reports") -> Path:
    directory = Path(reports_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_path = directory / f"{run_date}.md"
    file_path.write_text(content, encoding="utf-8")
    return file_path

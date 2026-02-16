from datetime import date
from pathlib import Path


async def generate_daily_report(
    run_date: date,
    decision_result: dict,
    eod_result: dict,
    sell_results: list[dict] | None = None,
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

    # Signal Sources
    signal_digest = decision_result.get("signal_digest", {})
    reddit_posts = decision_result.get("reddit_posts", 0)
    tickers_analyzed = decision_result.get("tickers_analyzed", 0)

    if signal_digest.get("source_type") == "multi":
        candidates = signal_digest.get("candidates", [])
        reddit_count = sum(1 for c in candidates if "reddit" in c.get("sources", []))
        screener_count = sum(1 for c in candidates if "screener" in c.get("sources", []))
        earnings_count = sum(1 for c in candidates if "earnings" in c.get("sources", []))
        multi_source = sum(1 for c in candidates if len(c.get("sources", [])) > 1)

        lines.append("## Signal Sources")
        lines.append(f"- Total candidates: {len(candidates)}")
        lines.append(f"- Reddit: {reddit_count} tickers ({reddit_posts} posts)")
        lines.append(f"- Screener: {screener_count} tickers")
        lines.append(f"- Earnings: {earnings_count} tickers")
        lines.append(f"- Multi-source: {multi_source} tickers")
        lines.append(f"- Tickers with market data: {tickers_analyzed}")
        lines.append("")
    else:
        lines.append("## Reddit Digest")
        lines.append(f"- Posts analyzed: {reddit_posts}")
        lines.append(f"- Tickers evaluated: {tickers_analyzed}")
        lines.append("")

    # Model Divergence
    real_exec = decision_result.get("real_execution", [])
    virtual_exec = decision_result.get("virtual_execution", [])
    real_tickers = {
        t.get("ticker") for t in real_exec if t.get("ticker") and t.get("status") != "skipped"
    }
    virtual_tickers = {
        t.get("ticker") for t in virtual_exec if t.get("ticker") and t.get("status") != "skipped"
    }
    if real_tickers or virtual_tickers:
        overlap = real_tickers & virtual_tickers
        main_only = real_tickers - virtual_tickers
        virtual_only = virtual_tickers - real_tickers

        lines.append("## Model Divergence")
        if overlap:
            lines.append(f"- Both picked: {', '.join(sorted(overlap))}")
        if main_only:
            lines.append(f"- {main_trader.capitalize()} only: {', '.join(sorted(main_only))}")
        if virtual_only:
            lines.append(f"- {virtual_trader.capitalize()} only: {', '.join(sorted(virtual_only))}")
        if not overlap and (main_only or virtual_only):
            lines.append("- Divergence: **Complete** (no overlap)")
        elif overlap and (main_only or virtual_only):
            lines.append("- Divergence: **Partial**")
        else:
            lines.append("- Divergence: **None** (identical picks)")
        lines.append("")

    # Picks & Execution
    lines.append("## Picks & Execution")

    # Main trader
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

    # Sell Triggers
    if sell_results:
        lines.append("## Sell Triggers")
        lines.append("| Ticker | LLM | Type | Return | Reason |")
        lines.append("|--------|-----|------|--------|--------|")
        for sell in sell_results:
            ticker = sell.get("ticker", "?")
            llm = sell.get("llm_name", "?")
            signal_type = sell.get("signal_type", "?")
            return_pct = sell.get("return_pct", 0)
            pnl_str = f"+{return_pct:.1f}%" if return_pct >= 0 else f"{return_pct:.1f}%"
            reasoning = sell.get("reasoning", "")
            lines.append(f"| {ticker} | {llm} | {signal_type} | {pnl_str} | {reasoning} |")
        lines.append("")

    # Summary
    approval = decision_result.get("approval", {})
    lines.append("## Summary")
    lines.append(f"- Approval: {approval.get('action', 'unknown')}")
    lines.append(f"- Real trades executed: {len(real_exec)}")
    lines.append(f"- Virtual trades executed: {len(virtual_exec)}")
    if sell_results:
        lines.append(f"- Sell triggers executed: {len(sell_results)}")
    lines.append("")

    return "\n".join(lines)


def write_daily_report(content: str, run_date: date, reports_dir: str = "reports") -> Path:
    directory = Path(reports_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_path = directory / f"{run_date}.md"
    file_path.write_text(content, encoding="utf-8")
    return file_path

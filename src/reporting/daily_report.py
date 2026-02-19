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

    # LLM Analysis — detailed reasoning per model
    pipeline_analysis = decision_result.get("pipeline_analysis", {})
    for trader_name in (main_trader, virtual_trader):
        analysis = pipeline_analysis.get(trader_name)
        if not analysis:
            continue
        role = "Main — Real" if trader_name == main_trader else "Virtual"
        lines.append(f"## {trader_name.capitalize()} Analysis ({role})")

        # Market summary from the LLM
        market_summary = analysis.get("market_summary", "")
        if market_summary:
            lines.append(f"**Market View:** {market_summary}")
            lines.append("")

        confidence = analysis.get("confidence", 0)
        lines.append(f"**Confidence:** {confidence:.0%}")
        lines.append("")

        # Why each stock was picked
        picks = analysis.get("picks", [])
        if picks:
            lines.append("### Picked")
            lines.append("| Ticker | Action | Allocation | Reasoning |")
            lines.append("|--------|--------|------------|-----------|")
            for p in picks:
                ticker = p.get("ticker", "?")
                action = p.get("action", "buy")
                alloc = p.get("allocation_pct", 0)
                reasoning = p.get("reasoning", "").replace("|", "/")
                lines.append(f"| {ticker} | {action} | {alloc:.0f}% | {reasoning} |")
            lines.append("")

        # Research scores for all analyzed tickers
        researched = analysis.get("researched_tickers", [])
        if researched:
            lines.append("### Research Scores")
            lines.append("| Ticker | Fund. | Tech. | Risk | Summary |")
            lines.append("|--------|-------|-------|------|---------|")
            for r in researched:
                ticker = r.get("ticker", "?")
                fund = r.get("fundamental_score", 0)
                tech = r.get("technical_score", 0)
                risk = r.get("risk_score", 0)
                summary = (r.get("summary") or r.get("catalyst") or "").replace("|", "/")
                # Truncate long summaries for table readability
                if len(summary) > 120:
                    summary = summary[:117] + "..."
                lines.append(f"| {ticker} | {fund:.1f} | {tech:.1f} | {risk:.1f} | {summary} |")
            lines.append("")

        # Why stocks were NOT picked
        not_picked = analysis.get("not_picked", [])
        if not_picked:
            lines.append("### Not Picked")
            lines.append("| Ticker | Fund. | Tech. | Risk | Why Not |")
            lines.append("|--------|-------|-------|------|---------|")
            for r in not_picked:
                ticker = r.get("ticker", "?")
                fund = r.get("fundamental_score", 0)
                tech = r.get("technical_score", 0)
                risk = r.get("risk_score", 0)
                # Build a short explanation from the scores
                reasons = []
                if risk >= 7:
                    reasons.append("high risk")
                if fund < 5:
                    reasons.append("weak fundamentals")
                if tech < 5:
                    reasons.append("weak technicals")
                if not reasons:
                    reasons.append("lower conviction vs picks")
                summary = (r.get("summary") or "").replace("|", "/")
                if summary:
                    reason_str = f"{'; '.join(reasons)} — {summary}"
                else:
                    reason_str = "; ".join(reasons)
                if len(reason_str) > 120:
                    reason_str = reason_str[:117] + "..."
                lines.append(f"| {ticker} | {fund:.1f} | {tech:.1f} | {risk:.1f} | {reason_str} |")
            lines.append("")

        # Risk review adjustments
        risk_review = analysis.get("risk_review", {})
        if risk_review:
            risk_notes = risk_review.get("risk_notes", "")
            adjustments = risk_review.get("adjustments", [])
            vetoed = risk_review.get("vetoed_tickers", [])
            if risk_notes or adjustments or vetoed:
                lines.append("### Risk Review")
                if risk_notes:
                    lines.append(f"**Notes:** {risk_notes}")
                if adjustments:
                    for adj in adjustments:
                        lines.append(f"- {adj}")
                if vetoed:
                    lines.append(f"- **Vetoed:** {', '.join(vetoed)}")
                lines.append("")

    # Picks & Execution
    lines.append("## Execution")

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

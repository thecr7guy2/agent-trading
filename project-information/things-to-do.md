# Trading Bot — Things To Do / Ideas

## Completed (Overhaul)

- [x] Remove LLM vs LLM competition — Claude always trades, no rotation
- [x] Replace DB with T212 API live for positions — no database needed
- [x] Single demo/practice account (T212 demo), configurable budget per run
- [x] MiniMax for research analysis, Claude Opus for final buy decisions
- [x] OpenInsider cluster buys as the only candidate source (replaces BAFIN — BAFIN didn't work)
- [x] Conviction scoring: delta_own_pct × title_multiplier × recency_decay
- [x] Trade fallback — if a stock isn't on T212, try the next best candidate
- [x] 3-day blacklist to stop buying same stocks every day
- [x] Parallel enrichment of all candidates (yfinance + news + insider history)
- [x] Clean daily report format (company names, amounts, signal sources, reasoning)
- [x] Current positions in daily report from T212 EOD snapshot
- [x] Skipped/Failed/Blacklisted section in daily report

---

## Potential Next Features

### Sell Automation
No automated sell logic exists. Options:
- Add stop-loss / take-profit rules back to the supervisor (was removed in overhaul)
- Or let Claude's `sell_recommendations` in `DailyPicks` drive sell orders

### Performance Tracking (no DB needed)
The bot has no historical P&L view. A lightweight solution: append daily snapshots to a JSON file.
- `snapshots/portfolio_history.json` — daily entry with total value, invested, unrealized P&L
- `scripts/perf.py` — show week/month P&L from snapshot file
- Could also parse existing `reports/YYYY-MM-DD.md` files to extract this

### Telegram Notifications — Richer Format
Currently Telegram sends a text summary. Could include the full buy table in the notification so you see exactly what was bought without opening the report.

### Prompt Tuning
After running for a few weeks, review what the bot is actually buying and whether the conviction scoring is working. Tune `trader_aggressive.md` based on observed patterns.

### Agent Trace / Debug Mode
A flag (`--trace` or env var) that logs every agent's full input/output to a file. Useful for debugging why Claude made a particular pick.

### Real Account Support
Currently demo only. To add a live account: add a second T212 client with `use_demo=False` and a separate budget config, then run both in parallel in the supervisor.

---

## Scratch Notes / Ideas

- If a ticker is not tradable on T212, the fallback executor already handles it automatically
- Notification when no stocks were bought (budget not spent) — currently logged, could Telegram alert
- The `sell_recommendations` field in DailyPicks is populated by Claude but not acted on — easy to wire up

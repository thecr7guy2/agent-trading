# Trading Bot — Things To Do / Ideas

## Completed (Overhaul)

- [x] Remove LLM vs LLM competition — Claude always trades, no rotation
- [x] Replace DB with T212 API live for positions — no database needed
- [x] Paper trading (demo) vs real trading — conservative (€10 real) + aggressive (€500 practice)
- [x] MiniMax for research/tool calls, Claude for decision making
- [x] OpenInsider cluster buys as a data source (replaces BAFIN — BAFIN didn't work)
- [x] Risk review agent added as Stage 4 (debates picks before buying)
- [x] Trade fallback — if a stock isn't on T212, try the next best candidate
- [x] 3-day blacklist to stop buying same stocks every day
- [x] Global screener (not just EU) with EU soft preference bonus
- [x] NewsAPI + FMP earnings revisions as enrichment data
- [x] Clean daily report format (company names, amounts, signal sources, reasoning)
- [x] Current positions with per-ticker P&L in daily report
- [x] Skipped/Failed/Blacklisted section in daily report

---

## Potential Next Features

### Performance Tracking (no DB needed)
The bot currently has no way to look back at historical P&L since there's no database. A lightweight solution: append daily snapshots to a JSON or CSV file.

- `snapshots/portfolio_history.json` — daily entry per account with total value, invested, unrealized P&L
- `scripts/perf.py` — show week/month P&L from snapshot file
- Could also parse the daily report markdown files to extract this data

### Telegram Notifications — Richer Format
Currently Telegram just sends a text summary. Could send the full formatted buy table as a message so you see exactly what was bought without opening the report file.

### Prompt Tuning
After running for a few weeks, review what the bot is actually buying and whether it's working. Tune the trader prompts based on patterns (e.g., if it always picks the same type of stocks, adjust the instructions).

### Agent Trace / Debug Mode
A flag (`--trace` or env var) that logs every agent's full input/output to a file. Useful for debugging why Claude made a particular pick or why research scored a ticker the way it did.

### Email Fallback
If Telegram is disabled, email the daily report using Python's smtplib. Cheaper than a Telegram bot for simple alerting.

---

## Scratch Notes / Ideas

- T212 has a demo/practice account — already implemented (aggressive strategy)
- For paper trades, if a stock is unknown/unavailable, try next candidate — already implemented (fallback executor)
- Notification when no stocks were bought (budget not spent) — currently logged, could Telegram alert

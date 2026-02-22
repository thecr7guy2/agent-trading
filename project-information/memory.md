# Trading Bot — Project Memory

## Current Status (as of Feb 2026)

**Overhaul complete.** The system runs fully autonomously as a single-strategy, single-account demo trading bot. No database.

---

## Architecture (Current)

- **No database** — positions from T212 API live, blacklist in `recently_traded.json`
- **Single strategy** — one demo/practice T212 account, `budget_per_run_eur` (default €1000/run)
- **No approval gate** — fully autonomous
- **2-stage pipeline:** MiniMax-M2.5 (research analyst) → Claude Opus 4.6 (trader/buy decision)
- **No strategy fan-out** — one pipeline, one account, one budget

---

## Key Implementation Notes

### Config (`src/config.py`)
Active fields:
- `anthropic_api_key`, `minimax_api_key`, `minimax_base_url`
- `t212_api_key` (demo account), `t212_api_secret` (optional)
- `budget_per_run_eur` (default 1000.0), `max_picks_per_run` (default 5)
- `insider_lookback_days` (default 3), `insider_top_n` (default 25), `min_insider_tickers` (default 10)
- `trade_every_n_days` (default 2), `pipeline_timeout_seconds` (default 900), `max_tool_rounds` (default 10)
- `recently_traded_path`, `recently_traded_days` (default 3)
- `claude_opus_model`, `claude_sonnet_model`, `claude_haiku_model`, `minimax_model`
- `scheduler_execute_time` (default `17:10`), `scheduler_eod_time` (default `17:35`)
- `news_api_key`, `fmp_api_key` (both optional)
- `telegram_bot_token`, `telegram_chat_id`, `telegram_enabled`

Removed: `eu_preference_bonus`, `t212_practice_api_key`, `practice_daily_budget_eur`, `max_candidates`, `sqlite_path`, `sell_*` settings, `scheduler_collect_times`

### Tests
- Tests use `SimpleNamespace` for settings mocks — when adding config fields, update fixtures in `test_scheduler.py` and `test_supervisor.py`
- Never hit real APIs in tests

### Models (`src/models.py`)
All Pydantic models live here (no DB). Key active models: `ResearchReport`, `ResearchFinding`, `DailyPicks`, `PickReview`, `StockPick`, `Position`.
Inactive/legacy models still present: `SentimentReport`, `TickerSentiment`, `MarketAnalysis`, `TickerAnalysis`.
`SellSignal` does NOT exist in the current codebase.

### Signal Sources
Only **OpenInsider** — `get_insider_candidates()` in `src/mcp_servers/market_data/insider.py`.
No Reddit, no screener sourcing, no BAFIN, no earnings calendar as candidate source.
Enrichment (parallel, per candidate): yfinance returns/fundamentals/technicals/earnings, insider history (30/60/90d), news (NewsAPI first, yfinance fallback).

### Trade Executor (`src/orchestrator/trade_executor.py`)
`execute_with_fallback()` tries candidates in rank order until budget is spent. Records successful buys in `recently_traded.json`.

### Daily Report (`src/reporting/daily_report.py`)
Auto-generated at EOD. Format: Summary → Buys → Skipped/Failed → Current Positions → P&L snapshot.

---

## LLMProvider Enum

`LLMProvider` (in `models.py`) identifies the strategy label, not the API:
- `CLAUDE` — default label
- `CLAUDE_AGGRESSIVE` — exists but currently unused (no strategy split in pipeline)

---

## Deleted in Overhaul

- `src/db/` — entire database layer
- `src/backtesting/` — backtesting engine
- `src/mcp_servers/market_data/bafin.py` — BAFIN scraper
- `src/mcp_servers/reddit/` — Reddit RSS scraper
- `src/orchestrator/approval.py` — approval flow
- `src/orchestrator/sell_strategy.py` — sell rule engine
- `src/reporting/leaderboard.py`, `pnl.py`
- `scripts/setup_db.py`, `scripts/backtest.py`, `scripts/run_sell_checks.py`

---

## Daily Schedule (Europe/Berlin, weekdays only)

Scheduler has **exactly 2 cron jobs**:
- **17:10** — `run_decision_cycle()` — insider digest → pipeline → buy orders
- **17:35** — `run_end_of_day()` → EOD snapshot + daily MD report

No Reddit collection, no sell checks, no mid-day jobs.

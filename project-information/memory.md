# Trading Bot — Project Memory

## Current Status (as of Feb 2026)

**Overhaul complete.** All 9 phases of the overhaul plan (`plans/overhaul.md`) implemented. The system is running fully autonomously with no database.

---

## Architecture (Post-Overhaul)

- **No database** — positions from T212 API live, blacklist in `recently_traded.json`
- **No LLM rotation** — Claude always trades, both strategies run every day
- **No approval gate** — fully autonomous, €10/day real money budget is the safety net
- **Two strategies:** Conservative (real, €10/day, T212 live) + Aggressive (practice, €500/day, T212 demo)
- **4-stage pipeline:** MiniMax-M2.5 (sentiment) → MiniMax-M2.5 with tools (research) → Claude Opus 4.6 (trader) → Claude Sonnet 4.6 (risk review)
- **Stages 1-2 shared** — run once, results handed to both strategies
- **Stages 3-4 fan out** — both strategies run in parallel using shared research

---

## Key Implementation Notes

### Config (`src/config.py`)
New fields added in overhaul:
- `news_api_key` — NewsAPI (optional)
- `fmp_api_key` — Financial Modeling Prep (optional)
- `recently_traded_path` — path to blacklist JSON (default: `recently_traded.json`)
- `recently_traded_days` — blacklist duration (default: 3)
- `max_candidates` — pipeline candidate limit (default: 15)
- `eu_preference_bonus` — soft scoring bonus for EU stocks (default: 0.1)

Removed: `sqlite_path`, `database_url`, `bafin_lookback_days`, `signal_candidate_limit`, `screener_min_market_cap`, `screener_exchanges`

### Tests
- Tests use `SimpleNamespace` for settings mocks — when adding config fields, update fixtures in `test_scheduler.py` and `test_supervisor.py`
- `test_reporting/test_daily_report.py` uses `patch("src.reporting.daily_report.get_settings", ...)`
- Never hit real APIs in tests

### Models (`src/models.py`)
All Pydantic models live here (no DB). Import from `src.models` everywhere. Key models: `SentimentReport`, `ResearchReport`, `ResearchFinding`, `DailyPicks`, `PickReview`, `StockPick`, `Position`, `SellSignal`.

### Signal Sources
- `screen_global_markets` MCP tool (was `screen_eu_markets`) — global, EU soft bonus
- `get_insider_activity` — OpenInsider cluster buys scraper
- `get_analyst_revisions` — FMP or yfinance fallback for earnings revisions
- `get_news` — NewsAPI or yfinance news fallback

### Trade Executor (`src/orchestrator/trade_executor.py`)
`execute_with_fallback()` tries candidates in rank order until budget is spent. Blacklists successful buys in `recently_traded.json`.

### Daily Report (`src/reporting/daily_report.py`)
Clean format: Summary → Today's Buys (tables with company, amount, price, signal sources, reasoning) → Skipped/Failed → Current Positions (from EOD raw positions) → Sell Triggers.

Uses `get_settings()` for budget values. EOD result must include `live_positions` and `demo_positions` (raw lists, not just aggregates).

---

## LLMProvider Enum

`LLMProvider` identifies the **strategy**, not the underlying API:
- `CLAUDE` = conservative strategy (real money, T212 live)
- `CLAUDE_AGGRESSIVE` = aggressive strategy (practice money, T212 demo)

Both strategies use MiniMax for stages 1-2 and Claude for stages 3-4.

---

## Deleted in Overhaul

- `src/db/` — entire database layer
- `src/backtesting/` — backtesting engine
- `src/mcp_servers/market_data/bafin.py` — BAFIN scraper (didn't work)
- `src/orchestrator/approval.py` — approval flow
- `src/reporting/leaderboard.py` and `pnl.py` — DB-dependent reporting
- `scripts/setup_db.py`, `scripts/backtest.py`, `scripts/run_daily.py`
- `trading_bot.db` — SQLite database file

---

## Daily Schedule (Europe/Berlin, weekdays)

- 08:00, 12:00, 16:30 — Reddit RSS collection
- 09:30, 12:30, 16:45 — Sell rule checks (stop-loss / take-profit / hold-period)
- 17:10 — Trade execution (signal digest → pipelines → buy orders)
- 17:35 — EOD snapshot + daily markdown report

---

## Things That Could Be Built Next

- **Performance tracking** — JSON or CSV log of daily P&L since no DB is available
- **Better Telegram alerts** — include the full buy table in notifications, not just a summary
- **Prompt tuning** — adjust trader/research prompts based on what the bot is buying
- **Deployment** — `DEPLOYMENT.md` in project-information has been updated for DB-free setup

# Trading Bot - Project Memory

## Project Status
- Phases 1-7 implemented
- Fully autonomous operation (no approval required)
- Scheduler runs 24/7 via APScheduler daemon (`scripts/run_scheduler.py`)

## Key Architecture Notes
- Existing tests use `SimpleNamespace` for settings mocks — when adding new config fields, update test fixtures in `test_scheduler.py`, `test_supervisor.py`, and `test_signal_digest.py`
- `reddit_sentiment` table exists in `001_initial.sql` (not a Phase 6 addition)
- Daily reports saved to `reports/YYYY-MM-DD.md`
- `require_approval=False` in scheduler (changed from True in Phase 6)

## Daily Schedule (Europe/Berlin)
- 08:00, 12:00, 16:30 — Reddit RSS collection
- 09:30, 12:30, 16:45 — Sell rule checks (stop-loss/take-profit/hold-period)
- 17:10 — Trade execution (buy decisions)
- 17:35 — End-of-day snapshot + daily MD report

## Model Optimization (Post Phase 8)
- Dropped Opus from pipeline — all Claude stages now use Sonnet 4.6 or Haiku 4.5
- Model assignment: Haiku (sentiment) → Sonnet 4.6 (research) → Sonnet 4.6 (trader) → Haiku (risk review)
- `claude_opus_model` config field removed; `claude_sonnet_model` default is `claude-sonnet-4-6`
- Prompt caching enabled via `_cached_system()` and `_cached_tools()` in `claude.py`
- 5-second tool round delay removed (SDK `max_retries=5` handles 429s automatically)
- `max_tool_rounds` default changed from 8 to 10
- Rate limits are per model class and identical for Opus/Sonnet (same RPM/ITPM/OTPM at every tier)

## Phase 7 Components (Multi-Source Signal Engine)
- `src/mcp_servers/market_data/screener.py` — EU screener via yfinance EquityQuery (gainers/losers/most_active)
- `src/mcp_servers/market_data/finance.py` — Added `get_ticker_news()`, `get_earnings_calendar_upcoming()`, `get_ticker_earnings()`
- `src/mcp_servers/market_data/server.py` — 4 new MCP tools: `screen_eu_markets`, `get_news`, `get_earnings_calendar`, `get_earnings`
- `src/orchestrator/supervisor.py` — `build_signal_digest()` merges Reddit + screener + earnings, enriches with news
- `src/mcp_servers/trading/portfolio.py` — `save_signal_source()` for `signal_sources` table
- `src/db/migrations/003_signal_sources.sql` — New table for tracking per-source signals
- Config: `signal_candidate_limit`, `screener_min_market_cap`, `screener_exchanges`
- Pipeline accepts `signal_digest` kwarg (backward compat with `reddit_digest`)
- Daily report shows "Signal Sources" section with per-source counts + "Model Divergence" section

## Phase 6 Components
- `src/orchestrator/sell_strategy.py` — SellStrategyEngine (stop-loss, take-profit, hold-period)
- `src/notifications/telegram.py` — TelegramNotifier (optional, no-op if disabled)
- `src/backtesting/engine.py` — BacktestEngine with SimulatedPortfolio
- `src/backtesting/data_source.py` — BacktestDataSource (reads reddit_sentiment + backtest tables)
- `scripts/run_scheduler.py` — Daemon script for 24/7 operation
- `scripts/run_sell_checks.py` — Manual sell check trigger
- `scripts/backtest.py` — CLI backtest runner
- `DEPLOYMENT.md` — Server migration guide (systemd, PostgreSQL, monitoring)

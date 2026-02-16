# Phase 6 — Autonomous Sell Automation & Backtesting

## Context

Phases 1-5 are complete in the repository:
- Data collection + MCP servers are running
- LLM pipelines and supervisor orchestration are implemented
- Reporting (terminal + markdown daily reports) is implemented
- **Scheduler runs fully autonomously** (no user approval required)

Phase 6 goals:
1. ✅ Automated sell strategy (stop-loss, take-profit, hold-period)
2. ✅ Historical backtesting mode
3. 📋 Telegram notifications (optional, notify-only)
4. ❌ Dashboard UI (deferred)

## Design Decisions

**Fully Autonomous Operation:**
- No approval gates for buys or sells (both execute automatically)
- Real and virtual portfolios managed identically
- Budget cap (€10/day) provides safety net
- Scheduled execution: 3x daily sentiment collection + 1x daily trade execution

**Sell Automation:**
- Evaluate sell rules 3x/day during market hours (09:30, 12:30, 16:45)
- Execute sells immediately when triggered (no approval queue)
- Rules: stop-loss (-10%), take-profit (+15%), hold-period (5 days)
- Sells logged to daily MD reports

**Backtesting:**
- Replay historical daily pipelines using persisted sentiment snapshots
- Simulate both LLM strategies over date ranges
- Evaluate performance metrics (P&L, win rate, best picks)

**Telegram (Optional):**
- Send daily summary notifications
- Send sell trigger alerts
- No interactive commands (notifications only)

## Scope

### In Scope
1. Sell strategy engine with configurable thresholds
2. Automated sell execution (real + virtual portfolios)
3. Scheduler integration for 3x daily sell checks
4. Sentiment snapshot persistence (for backtesting)
5. Backtesting engine with pipeline replay
6. Backtest script + MD report generation
7. Optional Telegram notifications (graceful no-op if disabled)
8. Tests for all Phase 6 modules

### Out of Scope
1. Approval queues or pending states
2. Telegram interactive commands
3. Dashboard UI
4. Live intraday streaming

## Public API / Interface Changes

### Config (`src/config.py`, `.env.example`)

Add:
```python
# Sell automation
sell_stop_loss_pct: float = 10.0
sell_take_profit_pct: float = 15.0
sell_max_hold_days: int = 5
sell_check_schedule: str = "09:30,12:30,16:45"

# Backtesting
backtest_start_date: str | None = None
backtest_end_date: str | None = None
backtest_daily_budget_eur: float = 10.0

# Telegram (optional)
telegram_enabled: bool = False
telegram_bot_token: str | None = None
telegram_chat_id: str | None = None
```

### DB Migration (`src/db/migrations/002_phase6_sell_backtest.sql`)

New tables:
```sql
-- Sentiment snapshots (for backtesting)
CREATE TABLE IF NOT EXISTS reddit_sentiment (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    scrape_date DATE NOT NULL,
    mention_count INTEGER DEFAULT 0,
    avg_sentiment NUMERIC(5,2),
    top_posts JSONB,
    subreddit_breakdown JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker, scrape_date)
);

-- Backtest runs
CREATE TABLE IF NOT EXISTS backtest_runs (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    notes TEXT
);

-- Backtest daily results
CREATE TABLE IF NOT EXISTS backtest_daily_results (
    id SERIAL PRIMARY KEY,
    run_id INTEGER REFERENCES backtest_runs(id) ON DELETE CASCADE,
    trade_date DATE NOT NULL,
    llm_name VARCHAR(50) NOT NULL,
    is_real BOOLEAN NOT NULL,
    invested NUMERIC(12,2) DEFAULT 0,
    value NUMERIC(12,2) DEFAULT 0,
    realized_pnl NUMERIC(12,2) DEFAULT 0,
    unrealized_pnl NUMERIC(12,2) DEFAULT 0,
    trades_json JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Note: No `sell_signal_queue` table needed (sells execute immediately).

### New Scripts

- `scripts/backtest.py` — Run historical backtests
- `scripts/run_sell_checks.py` — Manually trigger sell checks (for testing)

## Implementation Plan

### Step 1: Add Config Fields

Files:
- `src/config.py`
- `.env.example`

Add all new settings listed above.

### Step 2: Database Migration

Files:
- `src/db/migrations/002_phase6_sell_backtest.sql` (new)
- `scripts/setup_db.py` (extend to run migration)

Create tables for sentiment snapshots, backtest runs, and backtest results.

### Step 3: Sell Strategy Engine

Files:
- `src/orchestrator/sell_strategy.py` (new)

Implement:
- `SellSignal` Pydantic model (ticker, reason, trigger_price, quantity)
- `SellStrategyEngine` class with methods:
  - `evaluate_position(position: Position, current_price: float, open_date: date) -> SellSignal | None`
  - `evaluate_all_positions(positions: list[Position]) -> list[SellSignal]`

Rules:
- **Stop-loss:** Return % <= `-sell_stop_loss_pct`
- **Take-profit:** Return % >= `sell_take_profit_pct`
- **Hold-period:** Days held >= `sell_max_hold_days`

### Step 4: Sell Execution Integration

Files:
- `src/orchestrator/supervisor.py` (extend)

Add method:
```python
async def run_sell_checks(
    self,
    run_date: date | None = None,
    include_real: bool = True,
    include_virtual: bool = True
) -> dict
```

Behavior:
1. Fetch all open positions (real and/or virtual)
2. Get current prices via market data MCP
3. Evaluate sell rules using `SellStrategyEngine`
4. Execute sells immediately via Trading MCP
5. Return summary dict with executed sells

### Step 5: Scheduler Integration

Files:
- `src/orchestrator/scheduler.py`

Add 3 new jobs from `sell_check_schedule`:
```python
async def _run_sell_check_job(self) -> None:
    result = await self._supervisor.run_sell_checks()
    logger.info("Sell check finished: %s", result)
```

Configure jobs in `configure_jobs()` method.

### Step 6: Telegram Notifier (Optional)

Files:
- `src/notifications/telegram.py` (new)
- `src/notifications/__init__.py` (new)

Implement:
- `TelegramNotifier` class using `httpx` for Telegram Bot API
- `send_message(text: str) -> dict` (graceful no-op if disabled/missing credentials)

Notification events:
1. Daily decision summary (main trader, picks executed)
2. Sell triggers (ticker, reason, price, executed status)
3. Backtest completion summary

Integration points:
- `supervisor.run_decision_cycle()` — send daily summary
- `supervisor.run_sell_checks()` — send sell alerts
- `backtesting/engine.py` — send backtest completion

### Step 7: Sentiment Snapshot Persistence

Files:
- `src/orchestrator/supervisor.py` (extend `run_decision_cycle`)
- `src/db/repositories.py` (new helper module, optional)

At end of `run_decision_cycle()`:
- Extract ticker-level sentiment from digest
- Upsert rows to `reddit_sentiment` table (ticker, scrape_date, mention_count, avg_sentiment, top_posts)

### Step 8: Backtesting Engine

Files:
- `src/backtesting/engine.py` (new)
- `src/backtesting/data_source.py` (new)
- `src/backtesting/__init__.py` (new)

Components:

**DataSource:**
- `reconstruct_sentiment_digest(date: date) -> dict` — rebuild digest from `reddit_sentiment` table
- `get_historical_prices(tickers: list[str], date: date) -> dict` — use market MCP historical data

**BacktestEngine:**
- `run(start_date: date, end_date: date, run_name: str | None) -> BacktestRun`
- Iterate trading days in range
- For each day:
  - Reconstruct sentiment digest
  - Fetch historical market data
  - Apply rotation logic (which LLM is main/virtual)
  - Run both LLM pipelines
  - Simulate buy/sell execution using daily close prices
  - Apply sell strategy rules
  - Update simulated portfolios
  - Save results to `backtest_daily_results`

**Pydantic Models:**
- `BacktestRun` (id, name, start_date, end_date, status, summary)
- `BacktestDayResult` (trade_date, llm, portfolio_value, pnl, trades)

### Step 9: Backtest Script

Files:
- `scripts/backtest.py` (new)

CLI args:
- `--start YYYY-MM-DD` (required)
- `--end YYYY-MM-DD` (required)
- `--name` (optional, default: "backtest_YYYYMMDD")
- `--budget` (optional, default from config)

Output:
- Terminal summary table (P&L by LLM, win rate, best/worst picks)
- Markdown report at `reports/backtests/<run-name>.md`

### Step 10: Reporting Extensions

Files:
- `src/reporting/formatter.py` (extend)
- `src/reporting/daily_report.py` (extend)

Add sections to daily MD reports:
- **Sell Triggers:** List of sells executed today (ticker, reason, P&L)
- **Open Positions:** Current holdings with unrealized P&L and hold days

Add backtest report formatter:
- Leaderboard (LLM performance over backtest period)
- Cumulative P&L curve (table format)
- Best/worst picks

### Step 11: Manual Sell Check Script

Files:
- `scripts/run_sell_checks.py` (new)

CLI args:
- `--date YYYY-MM-DD` (optional, default today)
- `--real-only` / `--virtual-only` (optional)

Output:
- JSON summary of sells executed

## Test Plan

### New Test Files

- `tests/test_orchestrator/test_sell_strategy.py`
- `tests/test_orchestrator/test_sell_integration.py`
- `tests/test_backtesting/test_data_source.py`
- `tests/test_backtesting/test_engine.py`
- `tests/test_notifications/test_telegram.py`
- `tests/test_scripts/test_backtest_script.py`

### Updated Test Files

- `tests/test_orchestrator/test_scheduler.py` (add sell check jobs)
- `tests/test_orchestrator/test_supervisor.py` (add sell check integration)
- `tests/test_config.py` (new config fields)

### Test Scenarios

1. Stop-loss triggered at -10% executes sell
2. Take-profit triggered at +15% executes sell
3. Hold-period triggered at 5 days executes sell
4. Multiple sell signals for different positions execute in batch
5. Sell checks run without errors when no positions exist
6. Telegram notifier no-ops gracefully when disabled
7. Sentiment snapshot persistence and retrieval correctness
8. Backtest run creates run record + daily result rows
9. Backtest produces deterministic outputs for fixed mocked data
10. Scheduler integrates sell checks without blocking

## Rollout Sequence

1. Add config fields and DB migration
2. Implement sell strategy engine
3. Integrate sell checks into supervisor
4. Wire scheduler sell check jobs
5. Add Telegram notifier (optional)
6. Persist sentiment snapshots from daily cycles
7. Implement backtesting data source + engine
8. Create backtest script
9. Extend reporting with sell triggers
10. Run full test suite and dry-run scripts

## Acceptance Criteria

1. ✅ Sell checks run 3x daily via scheduler without blocking
2. ✅ Sells execute immediately when triggered (no approval required)
3. ✅ Daily sentiment persisted to `reddit_sentiment` table
4. ✅ Backtests complete over date ranges and output MD reports
5. ✅ Telegram notifications work when enabled, no-op when disabled
6. ✅ Daily MD reports include sell trigger section
7. ✅ Existing orchestration/reporting remains stable
8. ✅ All tests pass

## Risks and Mitigations

1. **Sell execution during non-market hours**
   - Mitigation: Schedule sell checks during market hours only (09:30-16:45)

2. **Historical sentiment sparsity**
   - Mitigation: Backtest explicitly skips dates with missing sentiment data

3. **Price staleness for virtual sells**
   - Mitigation: Use market data MCP for current prices, fallback to avg_buy_price if unavailable

4. **Over-trading due to frequent sell checks**
   - Mitigation: Track last sell check per position, cooldown period (1 hour)

## File Summary

### New Files

```
src/notifications/telegram.py
src/notifications/__init__.py
src/orchestrator/sell_strategy.py
src/backtesting/engine.py
src/backtesting/data_source.py
src/backtesting/__init__.py
src/db/migrations/002_phase6_sell_backtest.sql
scripts/backtest.py
scripts/run_sell_checks.py
tests/test_orchestrator/test_sell_strategy.py
tests/test_orchestrator/test_sell_integration.py
tests/test_backtesting/test_data_source.py
tests/test_backtesting/test_engine.py
tests/test_notifications/test_telegram.py
tests/test_scripts/test_backtest_script.py
```

### Modified Files

```
src/config.py
.env.example
src/orchestrator/supervisor.py
src/orchestrator/scheduler.py
src/reporting/formatter.py
src/reporting/daily_report.py
scripts/setup_db.py
tests/test_orchestrator/test_scheduler.py
tests/test_orchestrator/test_supervisor.py
tests/test_config.py
```

---

## Deployment Notes

After Phase 6 implementation:

1. Update `.env` with new sell automation settings
2. Run DB migration: `uv run python scripts/setup_db.py`
3. Restart scheduler service: `sudo systemctl restart trading-bot`
4. Monitor first sell check execution in logs
5. Optionally enable Telegram notifications

See `DEPLOYMENT.md` for full server deployment guide.

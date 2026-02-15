# Phase 4 Orchestration Plan

## Summary
Implement Phase 4 end-to-end orchestration for the trading bot with four deliverables: supervisor daily pipeline, LLM rotation logic, CLI approval flow, and APScheduler-based automation.
This plan also includes required integration updates so orchestration works safely in practice: trade attribution by LLM and dynamic ticker mapping for Trading 212 orders.

## Scope
In scope:
1. `/Users/sai/Documents/Projects/trading-bot/src/orchestrator/supervisor.py`
2. `/Users/sai/Documents/Projects/trading-bot/src/orchestrator/rotation.py`
3. `/Users/sai/Documents/Projects/trading-bot/src/orchestrator/approval.py`
4. `/Users/sai/Documents/Projects/trading-bot/src/orchestrator/scheduler.py`
5. `/Users/sai/Documents/Projects/trading-bot/scripts/run_daily.py`
6. `/Users/sai/Documents/Projects/trading-bot/src/config.py`
7. `/Users/sai/Documents/Projects/trading-bot/.env.example`
8. `/Users/sai/Documents/Projects/trading-bot/pyproject.toml`
9. `/Users/sai/Documents/Projects/trading-bot/src/mcp_servers/trading/server.py`
10. `/Users/sai/Documents/Projects/trading-bot/src/mcp_servers/trading/t212_client.py`
11. `/Users/sai/Documents/Projects/trading-bot/tests/test_orchestrator/test_rotation.py`
12. `/Users/sai/Documents/Projects/trading-bot/tests/test_orchestrator/test_approval.py`
13. `/Users/sai/Documents/Projects/trading-bot/tests/test_orchestrator/test_supervisor.py`
14. `/Users/sai/Documents/Projects/trading-bot/tests/test_orchestrator/test_scheduler.py`
15. `/Users/sai/Documents/Projects/trading-bot/tests/test_mcp_servers/test_trading.py`
16. `/Users/sai/Documents/Projects/trading-bot/plans/phase-4-orchestration.md` (save this plan text after approval)

Out of scope:
1. Telegram approvals (Phase 6)
2. Reporting engine and leaderboard formatting (Phase 5)
3. Sell automation rules (Phase 6)

## Public API / Interface Changes
1. `Settings` additions in `/Users/sai/Documents/Projects/trading-bot/src/config.py`:
   - `orchestrator_timezone: str = "Europe/Berlin"`
   - `approval_timeout_seconds: int = 120`
   - `approval_timeout_action: str = "approve_all"`
   - `market_data_ticker_limit: int = 12`
   - `scheduler_collect_times: str = "08:00,12:00,16:30"`
   - `scheduler_execute_time: str = "17:10"`
2. `place_buy_order` signature in `/Users/sai/Documents/Projects/trading-bot/src/mcp_servers/trading/server.py`:
   - From `(ticker: str, amount_eur: float)` to `(llm_name: str, ticker: str, amount_eur: float)`
3. `place_sell_order` signature in `/Users/sai/Documents/Projects/trading-bot/src/mcp_servers/trading/server.py`:
   - From `(ticker: str, quantity: float)` to `(llm_name: str, ticker: str, quantity: float)`
4. Trading 212 symbol resolution support in `/Users/sai/Documents/Projects/trading-bot/src/mcp_servers/trading/t212_client.py`:
   - Add cached metadata resolver for yfinance-style tickers (example: `ASML.AS`) to Trading 212 symbols (example: `ASML_NL_EQ`)

## Implementation Plan
1. Implement rotation logic in `/Users/sai/Documents/Projects/trading-bot/src/orchestrator/rotation.py`.
   - `get_main_trader(date)`: Mon/Wed/Fri = `claude`, Tue/Thu = `minimax`.
   - `get_virtual_trader(date)`: opposite provider.
   - `is_trading_day(date, timezone)`: skip weekends.
2. Implement CLI approval in `/Users/sai/Documents/Projects/trading-bot/src/orchestrator/approval.py`.
   - Prompt options: approve all, reject all, approve subset by pick index.
   - Timeout behavior uses selected policy: `approve_all`.
   - Return structured decision object used by supervisor.
3. Implement supervisor orchestration in `/Users/sai/Documents/Projects/trading-bot/src/orchestrator/supervisor.py`.
   - `collect_reddit_round()`: call Reddit MCP `collect_posts`.
   - `build_reddit_digest()`: call Reddit MCP `get_daily_digest`.
   - `build_market_data(digest)`: fetch price/fundamentals/technicals for top `market_data_ticker_limit` tickers.
   - Run Claude and MiniMax `AgentPipeline` concurrently with shared digest/market data and per-LLM portfolios.
   - Persist both LLM picks to `daily_picks` table with `is_main_trader`.
   - Run approval for main trader picks.
   - Execute main picks as real trades via updated trading server API.
   - Execute non-main picks as virtual trades.
   - Idempotency guard: if picks already stored for same `llm_name + date + ticker`, skip duplicate execution unless `force=True`.
4. Add trading symbol mapper and LLM attribution in trading server/client.
   - Resolve ticker via Trading 212 instruments metadata cache.
   - Retry/fallback order resolution logic: exact symbol, transformed EU mapping candidate, metadata lookup; skip unresolved ticker.
   - Record real trades with `llm_name` of current main trader instead of hardcoded `"real"`.
5. Implement scheduler in `/Users/sai/Documents/Projects/trading-bot/src/orchestrator/scheduler.py`.
   - Use APScheduler `AsyncIOScheduler` with timezone from config.
   - Weekday jobs:
     - `collect_round_1` at `08:00`
     - `collect_round_2` at `12:00`
     - `collect_round_3` at `16:30`
     - `decision_and_execution` at `17:10`
   - Job config: `coalesce=True`, `max_instances=1`, `misfire_grace_time=300`.
6. Update manual trigger script `/Users/sai/Documents/Projects/trading-bot/scripts/run_daily.py`.
   - Add CLI args: `--date`, `--no-approval`, `--force`, `--collect-rounds`.
   - Call supervisor `run_decision_cycle(...)` and print structured summary.
7. Update project config.
   - Add `apscheduler` dependency in `/Users/sai/Documents/Projects/trading-bot/pyproject.toml`.
   - Add new env vars to `/Users/sai/Documents/Projects/trading-bot/.env.example`.

## Data Flow
1. Scheduler/manual trigger starts supervisor run.
2. Supervisor collects or reads Reddit digest.
3. Supervisor enriches top tickers with market data.
4. Supervisor runs both LLM pipelines concurrently.
5. Supervisor saves both pick sets to DB.
6. Main trader picks go through CLI approval.
7. Approved main picks execute as real trades.
8. Other LLM picks are recorded as virtual trades.
9. Run summary is emitted for logs and script output.

## Edge Cases and Failure Handling
1. If one LLM pipeline fails, continue with the other; real trade execution only happens if main-trader pipeline succeeded.
2. If digest has no tickers, skip trade execution and return no-op summary.
3. If allocation totals exceed 100, normalize proportionally before execution.
4. If market price unavailable for virtual trade quantity calculation, skip that trade with explicit warning.
5. If ticker cannot be mapped to Trading 212 symbol, skip real order and continue remaining orders.
6. If approval times out, auto-approve all (selected policy).

## Test Cases
1. Rotation mapping by weekday and weekend skip behavior.
2. CLI approval: approve all, reject all, subset approve, timeout auto-approve-all.
3. Supervisor happy path with mocked MCP/LLM calls.
4. Supervisor partial failure path (one LLM fails).
5. Supervisor idempotency guard behavior with and without `force`.
6. Real order execution uses mapped Trading 212 symbol and passes `llm_name`.
7. Virtual trade quantity calculation from allocation and market price.
8. Scheduler registers all jobs with expected IDs, times, and timezone.
9. Updated trading server tests for new order signatures and mapping branch behavior.

## Acceptance Criteria
1. `scripts/run_daily.py` can execute one full decision cycle without code changes elsewhere.
2. Scheduler can run staged collection + end-of-day decision cycle weekdays.
3. Main-vs-virtual LLM behavior is deterministic and date-based.
4. Approval flow is functional from CLI and respects timeout policy.
5. Test suite passes for new orchestrator and updated trading interfaces.

## Assumptions and Defaults
1. Selected schedule mode: full-day staged orchestration.
2. Selected approval timeout policy: auto-approve all.
3. Selected ticker mapping strategy: dynamic metadata mapping.
4. Timezone default: `Europe/Berlin`.
5. Phase 4 does not include Telegram, reporting, or sell-rule automation.

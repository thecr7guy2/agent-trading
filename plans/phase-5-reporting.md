# Phase 5 — Reporting

## Context

Phases 1-4 are complete. The system collects Reddit data, runs LLM pipelines, executes real+virtual trades, and takes end-of-day portfolio snapshots. All data lives in PostgreSQL (`trades`, `positions`, `daily_picks`, `portfolio_snapshots`). What's missing: the ability to **view** results — P&L reports, leaderboard comparisons, formatted terminal output, and **daily markdown report files** written to `reports/` after each trading day.

## Scope

| # | Component | Description |
|---|-----------|-------------|
| 1 | `src/reporting/pnl.py` | P&L engine: aggregate trades + snapshots into `PnLReport` models |
| 2 | `src/reporting/leaderboard.py` | LLM comparison: rank by P&L, win rate, avg return |
| 3 | `src/reporting/formatter.py` | Terminal output with `rich` tables (matches spec box-drawing format) |
| 4 | `src/reporting/daily_report.py` | Generate + write markdown file per trading day to `reports/YYYY-MM-DD.md` |
| 5 | `scripts/report.py` | CLI: `--period day/week/month/all` |
| 6 | `scripts/run_daily.py` | After decision cycle + EOD, generate daily .md report |
| 7 | `src/orchestrator/scheduler.py` | Wire daily report generation into scheduled EOD job |
| 8 | Tests | Full coverage for all reporting modules |

## Implementation Order

---

### Step 1: Add `rich` dependency

```bash
uv add rich
```

---

### Step 2: P&L engine — `src/reporting/pnl.py`

**Reuses existing code:**
- `PortfolioManager.calculate_pnl()` — `src/mcp_servers/trading/portfolio.py:209`
- `PortfolioManager.get_positions_typed()` — `src/mcp_servers/trading/portfolio.py`
- `PortfolioManager.get_trade_history()` — `src/mcp_servers/trading/portfolio.py:177`
- `PnLReport` model — `src/db/models.py:143`
- `MCPToolClient` protocol — `src/orchestrator/mcp_client.py`

**Class: `PnLEngine`**
- `__init__(self, pm: PortfolioManager, market_client: MCPToolClient)`
- `async def get_pnl_report(llm_name, start_date, end_date, is_real) -> PnLReport`
  - Calls `pm.calculate_pnl()` for realized metrics (win/loss, proceeds)
  - Calls `pm.get_positions_typed()` + `market_client.call_tool("get_stock_price", ...)` for unrealized P&L
  - Returns fully populated `PnLReport` model
- `async def get_best_worst_picks(start_date, end_date) -> dict`
  - Queries trades in period via `pm.get_trade_history()` for each LLM
  - Gets current price for each ticker via market_client
  - Computes return % = (current - entry) / entry
  - Returns `{"best": {"ticker", "return_pct", "llm", "date"}, "worst": {...}}`
- `async def get_portfolio_summary(is_real=True) -> dict`
  - Aggregates positions across all LLMs where `is_real` matches
  - Returns `{"total_invested", "total_value", "pnl", "return_pct"}`

---

### Step 3: Leaderboard — `src/reporting/leaderboard.py`

**Class: `LeaderboardBuilder`**
- `__init__(self, pnl_engine: PnLEngine)`
- `async def build(start_date, end_date) -> list[dict]`
  - For each `LLMProvider`, calls `pnl_engine.get_pnl_report()` for both real + virtual combined
  - Computes avg return % = realized_pnl / total_invested
  - Sorts by total P&L descending
  - Returns ranked list: `[{"rank", "llm_name", "pnl", "win_rate", "avg_return", "total_trades"}]`

---

### Step 4: Terminal formatter — `src/reporting/formatter.py`

Uses `rich.console.Console` with `record=True` to capture as string, and `rich.table.Table` for tabular data.

**Functions:**
- `format_leaderboard(entries: list[dict]) -> str` — leaderboard table
- `format_portfolio_summary(summary: dict) -> str` — invested / value / P&L block
- `format_best_worst(best_worst: dict) -> str` — best/worst pick lines
- `format_full_report(period_label: str, leaderboard, summary, best_worst) -> str` — combines all into bordered output matching spec format
- `print_report(...)` — directly prints to terminal (convenience wrapper)

---

### Step 5: Daily markdown report — `src/reporting/daily_report.py`

**Function: `async def generate_daily_report(run_date, decision_result, eod_result, pm, market_client) -> str`**

Builds markdown from:
- `decision_result` — the dict returned by `supervisor.run_decision_cycle()`
- `eod_result` — the dict returned by `supervisor.run_end_of_day()`
- `pm` / `market_client` — for fetching daily picks details

**Markdown structure:**
```markdown
# Daily Trading Report — 2026-02-16 (Monday)

## Roles
- **Main Trader:** Claude (real money)
- **Virtual Trader:** MiniMax (paper trades)

## Reddit Digest
- Posts analyzed: 42
- Tickers evaluated: 5

## Picks & Execution
### Claude (Main — Real Trades)
| Ticker | Action | Allocation | Status |
|--------|--------|-----------|--------|
| ASML.AS | buy | 60% | filled |

### MiniMax (Virtual)
| Ticker | Action | Allocation | Status |
|--------|--------|-----------|--------|
| SAP.DE | buy | 100% | filled |

## Portfolio Snapshot
| Portfolio | Invested | Value | Unrealized P&L |
|-----------|----------|-------|----------------|
| claude_real | €25.00 | €26.50 | +€1.50 |
| claude_virtual | €25.00 | €24.80 | -€0.20 |
| minimax_real | €25.00 | €25.30 | +€0.30 |
| minimax_virtual | €25.00 | €25.10 | +€0.10 |

## Summary
- Approval: approve_all
- Real trades executed: 1
- Virtual trades executed: 1
```

**Function: `def write_daily_report(content: str, run_date: date, reports_dir: str = "reports") -> Path`**
- Creates `reports/` dir if it doesn't exist
- Writes to `reports/YYYY-MM-DD.md`
- Returns the Path

---

### Step 6: CLI script — `scripts/report.py`

```
uv run python -m scripts.report --period week
uv run python -m scripts.report --period day --date 2026-02-16
uv run python -m scripts.report --period month
uv run python -m scripts.report --period all
```

- `--period`: day / week / month / all (required)
- `--date`: optional, defaults to today. For week/month, uses as the end date
- Computes date range from period
- Creates DB pool → `PortfolioManager` → `PnLEngine` → `LeaderboardBuilder`
- Creates `InProcessMCPClient` via `create_market_data_client()` for live prices
- Calls formatter and prints to terminal

---

### Step 7: Wire daily reports into run_daily.py and scheduler

**File: `scripts/run_daily.py`**
- After `run_decision_cycle()`, also call `supervisor.run_end_of_day(run_date)`
- Pass both result dicts to `generate_daily_report()` + `write_daily_report()`
- Print report file path in output

**File: `src/orchestrator/scheduler.py`**
- In `_run_eod_job()`, after `supervisor.run_end_of_day()`, call `generate_daily_report()` + `write_daily_report()`
- Store the decision_result from `_run_decision_job()` on `self` so the EOD job can use it

---

### Step 8: Update `src/reporting/__init__.py`

Export: `PnLEngine`, `LeaderboardBuilder`, `generate_daily_report`, `write_daily_report`

---

### Step 9: Tests

**`tests/test_reporting/test_pnl.py`**
- `test_get_pnl_report` — mock PM + market client, verify PnLReport fields populated
- `test_get_pnl_report_no_positions` — empty portfolio returns zeroed report
- `test_get_best_worst_picks` — mock trades + prices, verify correct best/worst
- `test_get_portfolio_summary` — mock positions + prices, verify aggregation

**`tests/test_reporting/test_leaderboard.py`**
- `test_build_ranks_by_pnl` — verify ordering by P&L descending
- `test_build_empty` — no trades returns empty list

**`tests/test_reporting/test_formatter.py`**
- `test_format_leaderboard_contains_data` — verify table contains LLM names and P&L
- `test_format_full_report_has_all_sections` — verify period label, leaderboard, portfolio, best/worst

**`tests/test_reporting/test_daily_report.py`**
- `test_generate_daily_report_markdown` — verify sections present in output
- `test_write_daily_report_creates_file` — use `tmp_path`, verify file name and content
- `test_write_daily_report_creates_dir` — verify `reports/` dir created if missing

---

## Files Summary

**New files (8):**
- `src/reporting/pnl.py`
- `src/reporting/leaderboard.py`
- `src/reporting/formatter.py`
- `src/reporting/daily_report.py`
- `tests/test_reporting/test_pnl.py`
- `tests/test_reporting/test_leaderboard.py`
- `tests/test_reporting/test_formatter.py`
- `tests/test_reporting/test_daily_report.py`

**Modified files (5):**
- `src/reporting/__init__.py` — exports
- `scripts/report.py` — full CLI implementation
- `scripts/run_daily.py` — add EOD + daily report after decision cycle
- `src/orchestrator/scheduler.py` — wire report into EOD job
- `pyproject.toml` — `rich` dependency (via `uv add`)

## Verification

1. `uv run pytest tests/ -v` — all tests pass
2. `uv run ruff check src/ --fix && uv run ruff format src/` — clean
3. `uv run python -m scripts.report --period week` — prints formatted terminal report
4. `uv run python -m scripts.report --period day --date 2026-02-16` — prints single day
5. Manually check `reports/` folder gets a `.md` file after `run_daily.py`

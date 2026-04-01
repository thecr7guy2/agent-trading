# Trading Bot - Claude Code Instructions

## Project Overview

Signal-driven agentic trading system. Claude Opus reads enriched insider + politician buy data and decides what to buy — once per run, fully autonomous. Candidates come from two sources: **OpenInsider** (corporate cluster buys) and **Capitol Trades** (US politician disclosures), enriched with yfinance + news data. Trades execute automatically on a single T212 demo (practice) account with a configurable EUR budget. A scheduler daemon handles trade execution and EOD reporting autonomously. No database — all state comes from the T212 API and a small JSON blacklist file.

## Tech Stack

- **Language:** Python 3.13+
- **Package Manager:** `uv` (use `uv add` for deps, `uv run` to execute)
- **Async:** asyncio native (`async/await`, `asyncio.run()` entrypoint)
- **Storage:** No database — T212 API for positions, `recently_traded.json` for 3-day blacklist
- **Scheduler:** APScheduler (AsyncIOScheduler, cron-based jobs)
- **MCP SDK:** `mcp` (official Python SDK)
- **HTTP client:** `httpx` (async)
- **Validation:** `pydantic` v2
- **Linting/Formatting:** `ruff`
- **Testing:** `pytest` + `pytest-asyncio`

## Commands

```bash
# Install dependencies
uv sync

# Run linter and formatter
uv run ruff check src/ --fix
uv run ruff format src/

# Run tests
uv run pytest tests/ -v

# Run the scheduler daemon (24/7 autonomous operation)
uv run python scripts/run_scheduler.py

# Run full pipeline without placing orders (dry run)
uv run python scripts/dry_run.py
uv run python scripts/dry_run.py --budget 1500 --lookback 7

# Trigger one decision cycle immediately (places real demo orders)
uv run python scripts/run_daily.py

# Show current portfolio P&L from T212
uv run python scripts/report.py

# Show next scheduled job times
uv run python scripts/check_schedule.py

# Start an MCP server (for development/testing)
uv run python -m src.mcp_servers.market_data.server
uv run python -m src.mcp_servers.trading.server
```

## Autonomous Scheduler (Daily Schedule — Europe/Berlin)

The scheduler daemon (`scripts/run_scheduler.py`) runs jobs automatically:

| Time | Days | Job |
|------|------|-----|
| `17:10` | Tue + Fri | Trade execution — build digest, run Claude Opus, place orders |
| `17:35` | Tue + Fri | EOD snapshot + daily MD report + dashboard push |
| `10:00` | Mon–Fri | Lightweight portfolio snapshot → dashboard refresh |
| `15:30` | Mon–Fri | Lightweight portfolio snapshot → dashboard refresh |

All times configurable via `.env`. Trade days via `SCHEDULER_TRADE_DAYS` (default `tue,fri`). Snapshot times via `SCHEDULER_SNAPSHOT_TIMES`. The supervisor skips internally if fewer than `MIN_INSIDER_TICKERS` candidates are found.

## Code Style

- **Formatting/Linting:** Ruff handles both. Run `ruff check --fix` and `ruff format` before committing.
- **Type hints:** Use them on all function signatures and class attributes. No need for `mypy` — type hints are for readability and IDE support.
- **Imports:** Use absolute imports from `src.` prefix (e.g., `from src.agents.base_agent import BaseAgent`).
- **Async:** Default to async functions. All I/O (HTTP, APIs) must be async. Use `asyncio.gather()` for parallel operations.
- **Naming:** snake_case for functions/variables, PascalCase for classes, UPPER_SNAKE for constants.
- **No docstrings unless the logic is non-obvious.** Prefer clear function/variable names over comments.

## Project Structure

```
src/
├── mcp_servers/          # MCP server implementations (one per domain)
│   ├── market_data/      # yfinance, NewsAPI, OpenInsider, Capitol Trades, FMP
│   │   ├── server.py     # FastMCP server — all market data tools
│   │   ├── finance.py    # yfinance wrappers (price, fundamentals, technicals)
│   │   ├── screener.py   # Screener helpers (enrichment only, not candidate sourcing)
│   │   ├── news.py       # NewsAPI headlines
│   │   ├── insider.py    # OpenInsider scraper — corporate insider candidates
│   │   ├── capitol_trades.py  # Capitol Trades scraper — politician buy candidates
│   │   └── earnings.py   # FMP/yfinance earnings calendar
│   └── trading/          # Trading 212 API + portfolio helpers
│       ├── server.py     # FastMCP server — buy/sell/portfolio tools
│       ├── t212_client.py # T212 REST API client (demo account only)
│       └── portfolio.py  # get_demo_positions helper
├── agents/               # LLM agent pipeline (Claude Opus only)
│   ├── base_agent.py     # Abstract base — all agents implement this
│   ├── trader_agent.py   # Claude Opus — reads enriched candidates, outputs ranked buy list
│   ├── tool_executor.py  # Wraps MCPToolClient for agent tool calls with timeout + logging
│   ├── pipeline.py       # Single-stage pipeline: candidates → Claude Opus decision
│   ├── providers/        # LLM API wrappers (claude.py)
│   └── prompts/          # System prompts (trader_aggressive.md — only active prompt)
├── orchestrator/         # Supervisor, scheduling, rotation, execution
│   ├── supervisor.py     # Main orchestrator — builds digest, runs pipeline, executes trades
│   ├── scheduler.py      # APScheduler cron jobs
│   ├── trade_executor.py # execute_with_fallback — tries candidates until budget spent
│   ├── rotation.py       # Trading day check
│   └── mcp_client.py     # MCP tool client wrappers
├── utils/
│   └── recently_traded.py # 3-day blacklist file (recently_traded.json)
├── notifications/
│   └── telegram.py       # Telegram bot (notify-only, no-op if disabled)
├── reporting/
│   ├── formatter.py      # Rich terminal output
│   ├── daily_report.py   # Clean markdown daily report generation
│   └── dashboard.py      # GitHub Pages dashboard data management
└── models.py             # All Pydantic models (no DB dependencies)
└── config.py             # Pydantic Settings (reads .env)

scripts/
├── run_scheduler.py      # Daemon for 24/7 autonomous operation
├── run_daily.py          # Trigger one decision cycle manually
├── dry_run.py            # Full pipeline without placing orders
├── check_schedule.py     # Show next scheduled job times
└── report.py             # Live portfolio P&L from T212

reports/
└── YYYY-MM-DD.md         # Daily trading reports (auto-generated)

docs/
├── index.html            # GitHub Pages dashboard
└── data.json             # Dashboard data (auto-updated by scheduler)
```

## Architecture Rules

1. **No database.** Positions come from the T212 API live. The only persistence is `recently_traded.json` (3-day buy blacklist). Do not add any DB layer.
2. **MCP servers are tool providers only.** They expose tools via the MCP protocol. They do NOT contain business logic or make LLM calls. Keep them thin wrappers around external APIs.
3. **Agents are LLM wrappers.** Each agent calls one LLM API with a prompt and returns structured output (Pydantic models).
4. **The orchestrator ties everything together.** It runs the daily pipeline: collect signals → merge → enrich → Claude decision → execute with fallback → EOD report. All sequencing lives here.
5. **Config via environment variables.** All API keys and settings come from `.env` loaded by `src/config.py` using Pydantic Settings. Never hardcode secrets.
6. **Fully autonomous operation.** No approval gates — all buys execute automatically. The daily budget cap and `min_insider_tickers` threshold are the safety nets.
7. **Trade fallback.** If a buy fails (ticker unavailable, order rejected), the executor tries the next candidate until the budget is spent or all candidates are exhausted.
8. **Stock variety.** Tickers bought are blacklisted for 3 days (`recently_traded.json`) to prevent buying the same stocks repeatedly.

## Signal Sources

Candidates come from two sources, fetched in parallel and merged by ticker.

### OpenInsider (corporate insiders)

| Data | Source | Purpose |
|------|--------|---------|
| **OpenInsider** | `insider.py` — scrapes openinsider.com | Primary candidates (conviction-scored cluster buys) |
| **yfinance returns** | `finance.py` | 1m / 6m / 1y price returns per ticker |
| **yfinance fundamentals** | `finance.py` | P/E, market cap, margins, debt/equity |
| **yfinance technicals** | `finance.py` | RSI, MACD, Bollinger Bands |
| **yfinance earnings** | `earnings.py` | Upcoming earnings calendar |
| **OpenInsider history** | `insider.py` | 30/60/90-day insider buy counts, acceleration flag |
| **NewsAPI / yfinance news** | `news.py` / `finance.py` | Recent headlines (NewsAPI first, yfinance fallback) |

### Capitol Trades (politician disclosures)

- Scrapes `capitoltrades.com` for recent US Congressional buy disclosures
- Scored by trade size × recency decay
- Mega-caps (>$50B market cap) are filtered out — routine allocation, not signal
- If Capitol Trades candidates are available, at least 1 is guaranteed in the final output (`enforce_ct_pick`)
- Same ticker in both sources → merged into single candidate with combined conviction score and `source: "openinsider+capitol_trades"`

## Pydantic Models

All models live in `src/models.py`. Import from there everywhere — never use raw dicts for structured data.

**Pipeline models:**
- `DailyPicks` — Claude Opus output: ranked buy list with reasoning
- `PickReview` — wraps `DailyPicks` (risk review stage is inactive — DailyPicks goes straight in)
- `StockPick` — a single stock recommendation (ticker, action, allocation %, reasoning, source)

**Trading models:**
- `Position` — a current holding (sourced from T212, not DB)

## LLM Agent Pipeline (1 active stage)

| Stage | Agent | Provider | Model | Role |
|-------|-------|----------|-------|------|
| Trader | `TraderAgent` | Claude | Opus 4.6 | Portfolio manager — reads enriched candidates, outputs ranked buy list |

The pipeline is:
1. `build_insider_digest()` — OpenInsider + Capitol Trades candidates fetched in parallel, merged by ticker, enriched with yfinance + news
2. Blacklist filter + pool-aware cap (Capitol Trades reserved slots guaranteed)
3. `run_decision()` — Claude Opus reads all enriched data, outputs ranked buy list
4. `enforce_ct_pick()` — post-processing: injects top CT candidate if none selected
5. `execute_with_fallback()` — places orders on T212 demo, tries each pick in order until budget spent

### Provider Details
- **Claude:** `anthropic` SDK, Opus 4.6 for trader
- System prompt: `src/agents/prompts/trader_aggressive.md` — the only active prompt file

## Insider Conviction Scoring

### OpenInsider
Formula per transaction: `conviction_score = delta_own_pct × title_multiplier × recency_decay`
- **C-suite multiplier** — CEO/CFO/COO/President/CTO/Chairman get 3× weight; all others 1×
- **ΔOwn %** — stake increase as a % of existing holdings (`New` positions treated as 100%)
- **Recency decay** — `e^(-0.2 × days_since_trade)` — fresher buys score higher
- Only included if: cluster buy (2+ insiders) OR solo C-suite with ΔOwn ≥ 3%

### Capitol Trades
Formula per transaction: `conviction_score = trade_amount_midpoint × recency_decay`
- Grouped by ticker; scores summed across politicians
- Mega-caps (market cap > `CAPITOL_TRADES_MAX_MARKET_CAP`, default $50B) filtered out

## MCP Server Guidelines

- Each MCP server is a standalone process using the `mcp` Python SDK with FastMCP.
- Servers use stdio transport for local development.
- Keep tool granularity reasonable — one tool per logical action.

## Environment Variables

All required env vars are in `.env.example`. Key vars:

```
ANTHROPIC_API_KEY        # Required
T212_API_KEY             # Required (demo/practice account)
T212_API_SECRET          # Optional
NEWS_API_KEY             # Optional — falls back to yfinance news
FMP_API_KEY              # Optional — falls back to yfinance recommendations
TELEGRAM_BOT_TOKEN       # Optional — Telegram notifications
TELEGRAM_CHAT_ID         # Optional
TELEGRAM_ENABLED         # Optional (bool, default false)
```

Key tuning vars (all have defaults):
```
BUDGET_PER_RUN_EUR            # Default 1000.0
MAX_PICKS_PER_RUN             # Default 5
INSIDER_LOOKBACK_DAYS         # Default 5 — how far back to scrape OpenInsider
INSIDER_TOP_N                 # Default 25 — OpenInsider candidates scored
RESEARCH_TOP_N                # Default 15 — candidates passed to Claude
MIN_INSIDER_TICKERS           # Default 10 — skip run if fewer candidates found
RECENTLY_TRADED_DAYS          # Default 3 — blacklist window
CAPITOL_TRADES_ENABLED        # Default true
CAPITOL_TRADES_LOOKBACK_DAYS  # Default 3
CAPITOL_TRADES_TOP_N          # Default 10
CAPITOL_TRADES_RESERVED_SLOTS # Default 3 — CT slots guaranteed in research pool
CAPITOL_TRADES_MAX_MARKET_CAP # Default 50000000000 ($50B)
SCHEDULER_TRADE_DAYS          # Default tue,fri
SCHEDULER_EXECUTE_TIME        # Default 17:10
SCHEDULER_EOD_TIME            # Default 17:35
SCHEDULER_SNAPSHOT_TIMES      # Default 10:00,15:30
PIPELINE_TIMEOUT_SECONDS      # Default 900
```

## Git

- Simple descriptive commit messages in plain English.
- No feature branches needed — work on `master`.
- Don't commit `.env`, `__pycache__/`, or `.venv/`.

## Testing

- Use `pytest` with `pytest-asyncio` for async tests.
- Mock external APIs (yfinance, T212, LLMs) — never hit real APIs in tests.
- Test files mirror source structure: `tests/test_agents/`, `tests/test_mcp_servers/`, `tests/test_orchestrator/`, `tests/test_reporting/`, etc.
- When adding new config fields to `Settings`, update test fixtures that use `SimpleNamespace` mocks (check `test_scheduler.py` and `test_supervisor.py`).

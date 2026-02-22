# Trading Bot - Claude Code Instructions

## Project Overview

Insider-signal-driven agentic trading system. A 2-stage LLM pipeline uses MiniMax for research analysis and Claude Opus for final buy decisions. Candidates come exclusively from OpenInsider cluster buys, enriched with yfinance + news data. Trades execute automatically on a single T212 demo (practice) account with a configurable EUR budget. A scheduler daemon handles trade execution and EOD reporting autonomously. No database — all state comes from the T212 API and a small JSON blacklist file.

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

# Show current portfolio P&L from T212
uv run python scripts/report.py
uv run python scripts/report.py --account demo

# Start an MCP server (for development/testing)
uv run python -m src.mcp_servers.market_data.server
uv run python -m src.mcp_servers.trading.server
```

## Autonomous Scheduler (Daily Schedule — Europe/Berlin)

The scheduler daemon (`scripts/run_scheduler.py`) runs 2 jobs automatically on weekdays:

| Time  | Job              | Description                                                  |
|-------|------------------|--------------------------------------------------------------|
| 17:10 | Trade execution  | Build insider digest, run LLM pipeline, buy stocks           |
| 17:35 | EOD snapshot     | Portfolio snapshot + daily MD report generation              |

Times are configurable via `.env` (`SCHEDULER_EXECUTE_TIME`, `SCHEDULER_EOD_TIME`). No human approval is required. The supervisor skips internally if fewer than `MIN_INSIDER_TICKERS` candidates are found, or if fewer than `TRADE_EVERY_N_DAYS` trading days have passed since the last run.

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
│   ├── market_data/      # yfinance, NewsAPI, OpenInsider, FMP earnings
│   │   ├── server.py     # FastMCP server — all market data tools
│   │   ├── finance.py    # yfinance wrappers (price, fundamentals, technicals)
│   │   ├── screener.py   # Screener helpers (used for enrichment, not candidate sourcing)
│   │   ├── news.py       # NewsAPI headlines
│   │   ├── insider.py    # OpenInsider scraper — primary candidate source
│   │   └── earnings.py   # FMP/yfinance earnings calendar
│   └── trading/          # Trading 212 API + portfolio helpers
│       ├── server.py     # FastMCP server — buy/sell/portfolio tools
│       ├── t212_client.py # T212 REST API client (demo account only)
│       └── portfolio.py  # get_demo_positions helper
├── agents/               # LLM agent pipeline (2 active stages)
│   ├── base_agent.py     # Abstract base — all agent stages implement this
│   ├── research_agent.py  # Stage 1: MiniMax analyst — pros/cons/catalyst per ticker
│   ├── trader_agent.py   # Stage 2: Claude Opus — final buy decisions
│   ├── sentiment_agent.py # Unused — MiniMax sentiment scorer (not called in pipeline)
│   ├── market_agent.py   # Unused — legacy MiniMax fallback (no tools)
│   ├── risk_agent.py     # Unused — RiskReviewAgent exists but not called
│   ├── tool_executor.py  # Wraps MCPToolClient for agent tool calls with timeout + logging
│   ├── pipeline.py       # Runs research → decision
│   ├── providers/        # LLM API wrappers (claude.py, minimax.py)
│   └── prompts/          # Per-stage system prompts (markdown files)
├── orchestrator/         # Supervisor, scheduling, rotation, execution
│   ├── supervisor.py     # Main orchestrator — builds digest, runs pipeline, executes trades
│   ├── scheduler.py      # APScheduler cron jobs (decision + EOD only)
│   ├── trade_executor.py # execute_with_fallback — tries candidates until budget spent
│   ├── rotation.py       # Trading day check
│   └── mcp_client.py     # MCP tool client wrappers
├── utils/
│   └── recently_traded.py # 3-day blacklist file (recently_traded.json)
├── notifications/
│   └── telegram.py       # Telegram bot (notify-only, no-op if disabled)
├── reporting/
│   ├── formatter.py      # Rich terminal output
│   └── daily_report.py   # Clean markdown daily report generation
└── models.py             # All Pydantic models (no DB dependencies)
└── config.py             # Pydantic Settings (reads .env)

scripts/
├── run_scheduler.py      # Daemon for 24/7 autonomous operation
└── report.py             # Live portfolio P&L from T212

reports/
└── YYYY-MM-DD.md         # Daily trading reports (auto-generated)
```

## Architecture Rules

1. **No database.** Positions come from the T212 API live. The only persistence is `recently_traded.json` (3-day buy blacklist). Do not add any DB layer.
2. **MCP servers are tool providers only.** They expose tools via the MCP protocol. They do NOT contain business logic or make LLM calls. Keep them thin wrappers around external APIs.
3. **Agents are LLM wrappers.** Each agent calls one LLM API with a prompt + tools and returns structured output (Pydantic models). Agents do not talk to each other directly.
4. **The orchestrator ties everything together.** It runs the daily pipeline: collect insider signals → enrich → research → trade decision → execute with fallback → EOD report. All sequencing lives here.
5. **Config via environment variables.** All API keys and settings come from `.env` loaded by `src/config.py` using Pydantic Settings. Never hardcode secrets.
6. **Fully autonomous operation.** No approval gates — all buys execute automatically. The daily budget cap and `min_insider_tickers` threshold are the safety nets.
7. **Trade fallback.** If a buy fails (ticker unavailable, order rejected), the executor tries the next candidate until the budget is spent or all candidates are exhausted.
8. **Stock variety.** Tickers bought are blacklisted for 3 days (`recently_traded.json`) to prevent buying the same stocks repeatedly.

## Signal Sources

All candidates come from **OpenInsider only**. The supervisor calls `get_insider_candidates()` and enriches each candidate in parallel:

| Data | Source | Purpose |
|------|--------|---------|
| **OpenInsider** | `insider.py` — scrapes openinsider.com | Primary candidates (conviction-scored cluster buys) |
| **yfinance returns** | `finance.py` | 1m / 6m / 1y price returns per ticker |
| **yfinance fundamentals** | `finance.py` | P/E, market cap, margins, debt/equity |
| **yfinance technicals** | `finance.py` | RSI, MACD, Bollinger Bands |
| **yfinance earnings** | `earnings.py` | Upcoming earnings calendar |
| **OpenInsider history** | `insider.py` | 30/60/90-day insider buy counts, acceleration flag |
| **NewsAPI / yfinance news** | `news.py` / `finance.py` | Recent headlines (NewsAPI first, yfinance fallback) |

## Pydantic Models

All models live in `src/models.py`. Import from there everywhere — never use raw dicts for structured data.

**Pipeline stage models:**
- `ResearchReport` / `ResearchFinding` — Stage 1 output: per-ticker pros/cons/catalyst (MiniMax)
- `DailyPicks` — Stage 2 output: ranked buy list with reasoning (Claude Opus)
- `PickReview` — wraps `DailyPicks` directly (Stage 4 risk review is inactive)

**Trading models:**
- `StockPick` — a single stock recommendation (ticker, action, allocation %, reasoning)
- `Position` — a current holding (sourced from T212, not DB)

## LLM Agent Pipeline (2 active stages)

| Stage | Agent | Provider | Model | Role |
|-------|-------|----------|-------|------|
| 1 — Research | `ResearchAgent` | MiniMax | MiniMax-M2.5 | Analyst — pros/cons/catalyst only, no verdict |
| 2 — Trader | `TraderAgent` | Claude | Opus 4.6 | Portfolio manager — final buy decisions |
| ~~3 — Risk Review~~ | ~~`RiskReviewAgent`~~ | — | — | *(skipped — DailyPicks wrapped directly into PickReview)* |

Both stages share a single run per day. The pipeline is:
1. `build_insider_digest()` — OpenInsider candidates + parallel enrichment
2. `run_research()` — MiniMax analyses all candidates, returns pros/cons/catalyst per ticker
3. `run_decision()` — Claude Opus reads enriched data + MiniMax notes, outputs ranked buy list
4. `execute_with_fallback()` — places orders on T212 demo, tries each pick in order until budget spent

### Provider Details
- **MiniMax:** `openai` SDK (OpenAI-compatible), MiniMax-M2.5 model
- **Claude:** `anthropic` SDK, Opus 4.6 for trader
- System prompts live in `src/agents/prompts/` as markdown files, NOT as inline strings
- `TraderAgent` always uses `trader_aggressive.md` (single prompt, no strategy split)

## Insider Conviction Scoring

OpenInsider candidates are scored by `insider.py` before any enrichment.

Formula per transaction: `conviction_score = delta_own_pct × title_multiplier × recency_decay`
- **C-suite multiplier** — CEO/CFO/COO/President/CTO/Chairman get 3× weight; all others 1×
- **ΔOwn %** — stake increase as a % of existing holdings (`New` positions treated as 100%)
- **Recency decay** — `e^(-0.2 × days_since_trade)` — fresher buys score higher
- Scores are summed across all transactions per ticker
- Only included if: cluster buy (2+ insiders) OR solo C-suite with ΔOwn ≥ 3%

The top `INSIDER_TOP_N` (default 25) candidates by conviction score are passed to the pipeline.

## MCP Server Guidelines

- Each MCP server is a standalone process using the `mcp` Python SDK with FastMCP.
- Servers use stdio transport for local development.
- Keep tool granularity reasonable — one tool per logical action.

## Environment Variables

All required env vars are in `.env.example`. Key vars:

```
ANTHROPIC_API_KEY        # Required
MINIMAX_API_KEY          # Required
T212_API_KEY             # Required (demo/practice account)
T212_API_SECRET          # Optional (if T212 requires secret)
NEWS_API_KEY             # Optional — falls back to yfinance news
FMP_API_KEY              # Optional — falls back to yfinance recommendations
TELEGRAM_BOT_TOKEN       # Optional — Telegram notifications
TELEGRAM_CHAT_ID         # Optional
TELEGRAM_ENABLED         # Optional (bool, default false)
```

Key tuning vars (all have defaults):
```
BUDGET_PER_RUN_EUR       # Default 1000.0
MAX_PICKS_PER_RUN        # Default 5
INSIDER_LOOKBACK_DAYS    # Default 3 — how far back to scrape OpenInsider
INSIDER_TOP_N            # Default 25 — candidates passed to pipeline
MIN_INSIDER_TICKERS      # Default 10 — skip run if fewer candidates found
TRADE_EVERY_N_DAYS       # Default 2 — minimum trading days between runs
RECENTLY_TRADED_DAYS     # Default 3 — blacklist window
PIPELINE_TIMEOUT_SECONDS # Default 900
```

## Git

- Simple descriptive commit messages in plain English.
- No feature branches needed — work on `master`.
- Don't commit `.env`, `__pycache__/`, or `.venv/`.

## Testing

- Use `pytest` with `pytest-asyncio` for async tests.
- Mock external APIs (yfinance, T212, LLMs) — never hit real APIs in tests.
- Test files mirror source structure: `tests/test_agents/`, `tests/test_mcp_servers/`, `tests/test_orchestrator/`, `tests/test_reporting/`, etc.
- When adding new config fields to `Settings`, update test fixtures in `test_scheduler.py` and `test_supervisor.py` (they use `SimpleNamespace` mocks).

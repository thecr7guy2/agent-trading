# Trading Bot - Claude Code Instructions

## Project Overview

Hybrid multi-LLM agentic trading system for European stocks. A shared 4-stage pipeline uses MiniMax for cheap data-gathering (stages 1-2) and Claude for decision-making (stages 3-4). Two strategies run in parallel each day: conservative (Claude, real money ~€10/day via T212 live) and aggressive (Claude Aggressive, practice account ~€500/day via T212 demo). The system runs fully autonomously with no approval required — a scheduler daemon handles all collection, trading, sell checks, and reporting automatically.

## Tech Stack

- **Language:** Python 3.13+
- **Package Manager:** `uv` (use `uv add` for deps, `uv run` to execute)
- **Async:** asyncio native (`async/await`, `asyncio.run()` entrypoint)
- **Database:** PostgreSQL + `asyncpg`
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

# Run a specific test file
uv run pytest tests/test_agents/test_claude_agent.py -v

# Run the daily pipeline manually (autonomous, no approval)
uv run python scripts/run_daily.py --no-approval

# Run the scheduler daemon (24/7 autonomous operation)
uv run python scripts/run_scheduler.py

# Manually trigger sell checks
uv run python scripts/run_sell_checks.py
uv run python scripts/run_sell_checks.py --real-only
uv run python scripts/run_sell_checks.py --virtual-only

# Run a historical backtest
uv run python scripts/backtest.py --start 2025-01-01 --end 2025-01-31
uv run python scripts/backtest.py --start 2025-01-01 --end 2025-01-31 --name "jan_test" --budget 10.0

# Generate P&L report
uv run python scripts/report.py --period week

# Start an MCP server (for development/testing)
uv run python -m src.mcp_servers.reddit.server
uv run python -m src.mcp_servers.market_data.server
uv run python -m src.mcp_servers.trading.server

# Database migrations
uv run python scripts/setup_db.py
```

## Autonomous Scheduler (Daily Schedule — Europe/Berlin)

The scheduler daemon (`scripts/run_scheduler.py`) runs all jobs automatically on weekdays:

| Time  | Job                  | Description                                      |
|-------|----------------------|--------------------------------------------------|
| 08:00 | Reddit collection    | Scrape RSS feeds, build sentiment summaries      |
| 09:30 | Sell check           | Evaluate stop-loss/take-profit/hold-period rules |
| 12:00 | Reddit collection    | Second collection round                          |
| 12:30 | Sell check           | Mid-day sell evaluation                          |
| 16:30 | Reddit collection    | Final collection before market close              |
| 16:45 | Sell check           | Pre-close sell evaluation                        |
| 17:10 | Trade execution      | Run LLM pipelines, buy stocks (no approval)      |
| 17:35 | EOD snapshot         | Portfolio snapshot + daily MD report generation   |

All times are configurable via `.env`. No human approval is required — the system is fully autonomous with a €10/day budget cap as the safety net.

## Code Style

- **Formatting/Linting:** Ruff handles both. Run `ruff check --fix` and `ruff format` before committing.
- **Type hints:** Use them on all function signatures and class attributes. No need for `mypy` — type hints are for readability and IDE support.
- **Imports:** Use absolute imports from `src.` prefix (e.g., `from src.agents.base_agent import BaseAgent`).
- **Async:** Default to async functions. All I/O (database, HTTP, APIs) must be async. Use `asyncio.gather()` for parallel operations (e.g., fanning out to both LLMs simultaneously).
- **Naming:** snake_case for functions/variables, PascalCase for classes, UPPER_SNAKE for constants.
- **No docstrings unless the logic is non-obvious.** Prefer clear function/variable names over comments.

## Project Structure

```
src/
├── mcp_servers/          # MCP server implementations (one per domain)
│   ├── reddit/           # Reddit scraping + sentiment (RSS feeds)
│   ├── market_data/      # Yahoo Finance, EU stock data
│   └── trading/          # Trading 212 + portfolio tracking (portfolio.py)
├── agents/               # LLM agent pipeline (3 stages per provider)
│   ├── base_agent.py     # Abstract base — all agent stages implement this
│   ├── sentiment_agent.py # Stage 1: Reddit sentiment scoring
│   ├── market_agent.py   # Stage 2: fundamentals + technicals analysis
│   ├── trader_agent.py   # Stage 3: final buy/sell decisions
│   ├── pipeline.py       # Runs stages 1→2→3 for a given LLM provider
│   ├── providers/        # LLM API wrappers (claude.py, minimax.py)
│   └── prompts/          # Per-stage system prompts (markdown files)
├── orchestrator/         # Supervisor, scheduling, rotation, sell strategy
│   ├── supervisor.py     # Main orchestrator (decision cycle, sell checks, EOD)
│   ├── scheduler.py      # APScheduler cron jobs (collection, sells, trades, EOD)
│   ├── sell_strategy.py  # Automated sell rules (stop-loss, take-profit, hold-period)
│   ├── rotation.py       # LLM rotation logic (who trades real/virtual each day)
│   ├── approval.py       # CLI approval flow (not used in autonomous mode)
│   └── mcp_client.py     # MCP tool client wrappers
├── notifications/        # Optional notification integrations
│   └── telegram.py       # Telegram bot (notify-only, no-op if disabled)
├── backtesting/          # Historical backtesting engine
│   ├── engine.py         # BacktestEngine with SimulatedPortfolio
│   └── data_source.py    # Reads reddit_sentiment + backtest tables from DB
├── reporting/            # P&L, leaderboard, terminal formatting, daily reports
│   ├── formatter.py      # Rich terminal output
│   └── daily_report.py   # Markdown daily report generation
├── db/                   # asyncpg connection, models, migrations
│   ├── connection.py     # Singleton connection pool
│   ├── models.py         # All Pydantic models (pipeline, trading, sell signals)
│   └── migrations/       # SQL migration files (001_initial, 002_phase6_backtest)
└── config.py             # Pydantic Settings (reads .env)

scripts/
├── run_scheduler.py      # Daemon for 24/7 autonomous operation
├── run_daily.py          # Manual single-day pipeline trigger
├── run_sell_checks.py    # Manual sell check trigger
├── backtest.py           # CLI backtest runner (date range, budget)
├── report.py             # P&L report generator
└── setup_db.py           # Database migration runner

reports/
├── YYYY-MM-DD.md         # Daily trading reports (auto-generated)
└── backtests/            # Backtest result reports
```

## Architecture Rules

1. **MCP servers are tool providers only.** They expose tools via the MCP protocol. They do NOT contain business logic or make LLM calls. Keep them thin wrappers around external APIs.
2. **Agents are LLM wrappers.** Each agent calls one LLM API with a prompt + tools and returns structured output (Pydantic models). Agents do not talk to each other directly.
3. **The orchestrator ties everything together.** It runs the daily pipeline: collect data → fan out to agents → collect picks → execute trades → check sell rules. All sequencing and coordination lives here.
4. **Database access goes through the trading MCP server or `src/db/` directly.** No raw SQL scattered in agent or orchestrator code. The `PortfolioManager` in `src/mcp_servers/trading/portfolio.py` handles all position/trade/sentiment DB operations.
5. **Config via environment variables.** All API keys, DB URLs, and settings come from `.env` loaded by `src/config.py` using Pydantic Settings. Never hardcode secrets.
6. **Fully autonomous operation.** No approval gates — all buys and sells execute automatically. The daily budget cap (€10) is the safety net. Sell rules (stop-loss, take-profit, hold-period) run 3x daily.

## Pydantic Models

Use Pydantic v2 models for all data flowing between components:

**Pipeline stage models (passed between agent stages):**
- `SentimentReport` — Stage 1 output: ranked tickers with sentiment scores, mention counts, key quotes
- `MarketAnalysis` — Stage 2 output: each ticker scored on fundamentals, technicals, and risk

**Trading models:**
- `StockPick` — a single stock recommendation from an LLM
- `DailyPicks` — Stage 3 output / final pipeline output: list of picks + metadata
- `Trade` — a recorded trade (real or virtual)
- `Position` — a current holding
- `SellSignal` — automated sell trigger (ticker, signal type, trigger price, return %)
- `PnLReport` — profit/loss summary for a period

Define these in `src/db/models.py` and import everywhere. Do NOT use raw dicts for structured data.

## Sell Strategy Rules

The `SellStrategyEngine` evaluates positions 3x daily (09:30, 12:30, 16:45) with these rules (evaluated in priority order):

1. **Stop-loss:** Sell if position return ≤ `-SELL_STOP_LOSS_PCT` (default: -10%)
2. **Take-profit:** Sell if position return ≥ `SELL_TAKE_PROFIT_PCT` (default: +15%)
3. **Hold-period:** Sell if held ≥ `SELL_MAX_HOLD_DAYS` days (default: 5)

All sells execute immediately for both real and virtual portfolios. No approval queue.

## Backtesting

The backtesting engine replays historical trading days using persisted sentiment snapshots:

- Reconstructs Reddit digest from `reddit_sentiment` table
- Fetches historical market data via market MCP
- Runs both LLM pipelines per day
- Applies sell strategy rules to simulated portfolios
- Results saved to `backtest_runs` + `backtest_daily_results` tables
- Markdown reports output to `reports/backtests/`

Sentiment snapshots are automatically persisted at the end of each daily decision cycle.

## LLM Agent Guidelines

### Pipeline Architecture (4 stages, hybrid providers)

One `AgentPipeline` instance runs per strategy. Both strategies share the same structure:

| Stage | Agent | Provider | Model | Tools |
|-------|-------|----------|-------|-------|
| 1 — Sentiment | `SentimentAgent` | MiniMax | MiniMax-M2.5 | None |
| 2 — Research | `ResearchAgent` | MiniMax | MiniMax-M2.5 | Market data MCP tools |
| 3 — Trader | `TraderAgent` | Claude | Opus 4.6 | None |
| 4 — Risk Review | `RiskReviewAgent` | Claude | Sonnet 4.6 | None |

Two strategies run in parallel each trading day:
- **Conservative** (`LLMProvider.CLAUDE`) — real money, €10/day budget, T212 live account
- **Aggressive** (`LLMProvider.CLAUDE_AGGRESSIVE`) — practice money, €500/day budget, T212 demo account

The `LLMProvider` enum identifies the strategy/portfolio, not the underlying API. Both strategies always use MiniMax for stages 1-2 and Claude for stages 3-4.

### Provider Details
- **MiniMax:** `openai` SDK (OpenAI-compatible, custom `base_url`), MiniMax-M2.5 for both sentiment and research
- **Claude:** `anthropic` SDK, Opus 4.6 for trading decisions, Sonnet 4.6 for risk review
- `providers/claude.py` and `providers/minimax.py` handle API specifics (auth, model selection, response parsing)
- Use structured output / JSON mode where available. Fall back to prompt-based JSON extraction with Pydantic validation
- System prompts live in `src/agents/prompts/` as markdown files (one per stage), NOT as inline strings

## MCP Server Guidelines

- Each MCP server is a standalone process using the `mcp` Python SDK.
- Servers use stdio transport for local development.
- Each tool function should be async and well-typed with Pydantic input/output models.
- Keep tool granularity reasonable — one tool per logical action, not one giant "do everything" tool.

## Git

- Simple descriptive commit messages in plain English.
- No feature branches needed — work on `main`.
- Don't commit `.env`, `__pycache__/`, or `.venv/`.

## Environment Variables

All required env vars are listed in `.env.example`. The app will fail fast on startup if required vars are missing (enforced by Pydantic Settings).

Key vars: `ANTHROPIC_API_KEY`, `MINIMAX_API_KEY`, `T212_API_KEY`, `DATABASE_URL`.

Sell automation vars: `SELL_STOP_LOSS_PCT`, `SELL_TAKE_PROFIT_PCT`, `SELL_MAX_HOLD_DAYS`, `SELL_CHECK_SCHEDULE`.

Optional: `TELEGRAM_ENABLED`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## Testing

- Use `pytest` with `pytest-asyncio` for async tests.
- Mock external APIs (Reddit, Yahoo Finance, Trading 212, LLMs) in tests — never hit real APIs in CI.
- Test files mirror source structure: `tests/test_agents/`, `tests/test_mcp_servers/`, `tests/test_orchestrator/`, `tests/test_backtesting/`, `tests/test_notifications/`, etc.
- Focus tests on: agent output parsing, P&L calculations, rotation logic, portfolio tracking, sell strategy rules, backtesting simulation.
- Existing tests use `SimpleNamespace` for settings mocks — when adding new config fields, also update the test fixtures in `tests/test_orchestrator/test_scheduler.py` and `tests/test_orchestrator/test_supervisor.py`.

## Deployment

See `DEPLOYMENT.md` for the complete server migration guide covering:
- Ubuntu server setup, PostgreSQL configuration
- systemd service (`trading-bot.service`) for 24/7 daemon operation
- Log rotation, monitoring, backup strategy
- Security checklist and troubleshooting

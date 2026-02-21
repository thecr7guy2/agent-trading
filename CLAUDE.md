# Trading Bot - Claude Code Instructions

## Project Overview

Hybrid multi-LLM agentic trading system for global stocks with a soft preference for EU listings. A shared 4-stage pipeline uses MiniMax for cheap data-gathering (stages 1-2) and Claude for decision-making (stages 3-4). Two strategies run in parallel each day: conservative (Claude, real money ~€10/day via T212 live) and aggressive (Claude Aggressive, practice account ~€500/day via T212 demo). The system runs fully autonomously — a scheduler daemon handles all collection, trading, sell checks, and reporting automatically. No database — all state comes from T212 live API and a small JSON blacklist file.

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

# Manually trigger sell checks
uv run python scripts/run_sell_checks.py

# Show current portfolio P&L from T212
uv run python scripts/report.py
uv run python scripts/report.py --account live
uv run python scripts/report.py --account demo

# Start an MCP server (for development/testing)
uv run python -m src.mcp_servers.reddit.server
uv run python -m src.mcp_servers.market_data.server
uv run python -m src.mcp_servers.trading.server
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

All times are configurable via `.env`. No human approval is required.

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
│   ├── reddit/           # Reddit scraping + sentiment (RSS feeds)
│   └── market_data/      # yfinance, NewsAPI, OpenInsider, FMP earnings
│       ├── server.py     # FastMCP server — all market data tools
│       ├── finance.py    # yfinance wrappers (price, fundamentals, technicals)
│       ├── screener.py   # Global market screener (EU soft bonus)
│       ├── news.py       # NewsAPI headlines
│       ├── insider.py    # OpenInsider cluster buy scraper
│       └── earnings.py   # FMP/yfinance earnings revisions
│   └── trading/          # Trading 212 API + portfolio helpers
│       ├── server.py     # FastMCP server — buy/sell/portfolio tools
│       ├── t212_client.py # T212 REST API client (live + demo)
│       └── portfolio.py  # get_live_positions / get_demo_positions helpers
├── agents/               # LLM agent pipeline (3 active stages)
│   ├── base_agent.py     # Abstract base — all agent stages implement this
│   ├── sentiment_agent.py # Stage 1: Reddit sentiment scoring (MiniMax)
│   ├── research_agent.py  # Stage 2: fundamentals + technicals with tools (MiniMax)
│   ├── trader_agent.py   # Stage 3: final buy decisions (Claude Opus)
│   ├── market_agent.py   # Legacy Stage 2 fallback (no tools — used if MCP unavailable)
│   ├── risk_agent.py     # Stage 4: risk review (exists but inactive — skipped in pipeline)
│   ├── tool_executor.py  # Wraps MCPToolClient for agent tool calls with timeout + logging
│   ├── pipeline.py       # Runs stages 1→2→3 (research shared, decision per strategy)
│   ├── providers/        # LLM API wrappers (claude.py, minimax.py)
│   └── prompts/          # Per-stage system prompts (markdown files)
├── orchestrator/         # Supervisor, scheduling, rotation, sell strategy
│   ├── supervisor.py     # Main orchestrator (decision cycle, sell checks, EOD)
│   ├── scheduler.py      # APScheduler cron jobs
│   ├── sell_strategy.py  # Automated sell rules (stop-loss, take-profit, hold-period)
│   ├── trade_executor.py # execute_with_fallback — tries candidates until budget spent
│   ├── rotation.py       # Trading day check + strategy assignment
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
├── run_sell_checks.py    # Manual sell check trigger
└── report.py             # Live portfolio P&L from T212

reports/
└── YYYY-MM-DD.md         # Daily trading reports (auto-generated)
```

## Architecture Rules

1. **No database.** Positions come from the T212 API live. The only persistence is `recently_traded.json` (3-day buy blacklist). Do not add any DB layer.
2. **MCP servers are tool providers only.** They expose tools via the MCP protocol. They do NOT contain business logic or make LLM calls. Keep them thin wrappers around external APIs.
3. **Agents are LLM wrappers.** Each agent calls one LLM API with a prompt + tools and returns structured output (Pydantic models). Agents do not talk to each other directly.
4. **The orchestrator ties everything together.** It runs the daily pipeline: collect signals → research → trade decision → execute with fallback → sell checks → report. All sequencing lives here.
5. **Config via environment variables.** All API keys and settings come from `.env` loaded by `src/config.py` using Pydantic Settings. Never hardcode secrets.
6. **Fully autonomous operation.** No approval gates — all buys and sells execute automatically. The daily budget cap (€10 real / €500 demo) is the safety net.
7. **Trade fallback.** If a buy fails (ticker unavailable, order rejected), the executor tries the next candidate until the budget is spent or all candidates are exhausted.
8. **Stock variety.** Tickers bought are blacklisted for 3 days (`recently_traded.json`) to prevent buying NVDA/MSFT every day.

## Signal Sources

Signals feed into a candidate list (max 15 tickers) that the research pipeline enriches:

| Source | What it provides | How it's used |
|--------|------------------|---------------|
| **yfinance screener** | Top movers, gainers, most active | Candidate tickers (global, EU gets +10% soft bonus) |
| **OpenInsider** | Cluster insider buys (2+ execs buying same stock) | Direct candidate — highest conviction signal |
| **Reddit RSS** | Social sentiment + mention counts | Supplementary — boosts multi-source candidates |
| **NewsAPI** | Recent headlines per ticker | Enrichment — MiniMax reads this during research |
| **FMP / yfinance** | Analyst estimate revisions (up/down trend) | Enrichment — earnings momentum signal |
| **yfinance calendar** | Upcoming earnings announcements | Candidate signal for near-term catalysts |

## Pydantic Models

All models live in `src/models.py`. Import from there everywhere — never use raw dicts for structured data.

**Pipeline stage models:**
- `SentimentReport` — Stage 1 output: tickers with sentiment scores and mentions
- `ResearchReport` / `ResearchFinding` — Stage 2 output: per-ticker research with pros/cons, scores
- `DailyPicks` — Stage 3 output: ranked buy list with reasoning
- `PickReview` — Stage 4 output: risk-reviewed picks with veto list

**Trading models:**
- `StockPick` — a single stock recommendation (ticker, action, allocation %, reasoning)
- `Position` — a current holding (sourced from T212, not DB)
- `SellSignal` — automated sell trigger (ticker, signal type, trigger price, return %)

## LLM Agent Pipeline (3 active stages, hybrid providers)

Both strategies share the same structure. Stages 1-2 run once (shared research); stage 3 fans out to both strategies in parallel. Stage 4 (Risk Review) is inactive — `DailyPicks` is wrapped directly into `PickReview` by `run_decision()`.

| Stage | Agent | Provider | Model | Tools |
|-------|-------|----------|-------|-------|
| 1 — Sentiment | `SentimentAgent` | MiniMax | MiniMax-M2.5 | None |
| 2 — Research | `ResearchAgent` | MiniMax | MiniMax-M2.5 | Market data MCP tools |
| 3 — Trader | `TraderAgent` | Claude | Opus 4.6 | None |
| ~~4 — Risk Review~~ | ~~`RiskReviewAgent`~~ | — | — | *(skipped)* |

Two strategies run in parallel each trading day:
- **Conservative** (`LLMProvider.CLAUDE`) — real money, €10/day, T212 live account
- **Aggressive** (`LLMProvider.CLAUDE_AGGRESSIVE`) — practice money, €500/day, T212 demo account

The `LLMProvider` enum identifies the strategy, not the underlying API. Both strategies always use MiniMax for stages 1-2 and Claude for stages 3-4.

### Provider Details
- **MiniMax:** `openai` SDK (OpenAI-compatible), MiniMax-M2.5 model
- **Claude:** `anthropic` SDK, Opus 4.6 for trader, Sonnet 4.6 for risk review
- System prompts live in `src/agents/prompts/` as markdown files, NOT as inline strings

## Sell Strategy Rules

The `SellStrategyEngine` evaluates positions 3x daily (09:30, 12:30, 16:45):

1. **Stop-loss:** Sell if return ≤ `-SELL_STOP_LOSS_PCT` (default: -10%)
2. **Take-profit:** Sell if return ≥ `SELL_TAKE_PROFIT_PCT` (default: +15%)
3. **Hold-period:** Sell if held ≥ `SELL_MAX_HOLD_DAYS` days (default: 5)

Positions are fetched live from T212 API — no DB reads. Sells execute immediately.

## MCP Server Guidelines

- Each MCP server is a standalone process using the `mcp` Python SDK with FastMCP.
- Servers use stdio transport for local development.
- Keep tool granularity reasonable — one tool per logical action.

## Environment Variables

All required env vars are in `.env.example`. Key vars:

```
ANTHROPIC_API_KEY        # Required
MINIMAX_API_KEY          # Required
T212_API_KEY             # Required (live account)
T212_PRACTICE_API_KEY    # Optional (demo account — enables aggressive strategy)
NEWS_API_KEY             # Optional — falls back to yfinance news
FMP_API_KEY              # Optional — falls back to yfinance recommendations
```

## Git

- Simple descriptive commit messages in plain English.
- No feature branches needed — work on `master`.
- Don't commit `.env`, `__pycache__/`, or `.venv/`.

## Testing

- Use `pytest` with `pytest-asyncio` for async tests.
- Mock external APIs (Reddit, yfinance, T212, LLMs) — never hit real APIs in tests.
- Test files mirror source structure: `tests/test_agents/`, `tests/test_mcp_servers/`, `tests/test_orchestrator/`, `tests/test_reporting/`, etc.
- When adding new config fields to `Settings`, update test fixtures in `test_scheduler.py` and `test_supervisor.py` (they use `SimpleNamespace` mocks).

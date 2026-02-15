# Trading Bot - Claude Code Instructions

## Project Overview

Multi-LLM agentic trading system for European stocks. Two LLMs (Claude and MiniMax 2.5) alternate daily as the "main trader" placing real trades (~10 EUR/day) via Trading 212, while the other makes virtual picks tracked in PostgreSQL. See `specs.md` for full architecture.

## Tech Stack

- **Language:** Python 3.12+
- **Package Manager:** `uv` (use `uv add` for deps, `uv run` to execute)
- **Async:** asyncio native (`async/await`, `asyncio.run()` entrypoint)
- **Database:** PostgreSQL + `asyncpg`
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

# Run the daily pipeline manually
uv run python scripts/run_daily.py

# Generate P&L report
uv run python scripts/report.py --period week

# Start an MCP server (for development/testing)
uv run python -m src.mcp_servers.reddit.server
uv run python -m src.mcp_servers.market_data.server
uv run python -m src.mcp_servers.trading.server

# Database migrations
uv run python scripts/setup_db.py
```

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
│   ├── reddit/           # Reddit scraping + sentiment
│   ├── market_data/      # Yahoo Finance, EU stock data
│   └── trading/          # Trading 212 + portfolio tracking
├── agents/               # LLM agent pipeline (3 stages per provider)
│   ├── base_agent.py     # Abstract base — all agent stages implement this
│   ├── sentiment_agent.py # Stage 1: Reddit sentiment scoring
│   ├── market_agent.py   # Stage 2: fundamentals + technicals analysis
│   ├── trader_agent.py   # Stage 3: final buy/sell decisions
│   ├── pipeline.py       # Runs stages 1→2→3 for a given LLM provider
│   ├── providers/        # LLM API wrappers (claude.py, minimax.py)
│   └── prompts/          # Per-stage system prompts (markdown files)
├── orchestrator/          # Supervisor, scheduling, rotation, approval
├── reporting/             # P&L, leaderboard, terminal formatting
├── db/                    # asyncpg connection, models, migrations
└── config.py              # Pydantic Settings (reads .env)
```

## Architecture Rules

1. **MCP servers are tool providers only.** They expose tools via the MCP protocol. They do NOT contain business logic or make LLM calls. Keep them thin wrappers around external APIs.
2. **Agents are LLM wrappers.** Each agent calls one LLM API with a prompt + tools and returns structured output (Pydantic models). Agents do not talk to each other directly.
3. **The orchestrator ties everything together.** It runs the daily pipeline: collect data → fan out to agents → collect picks → execute trades. All sequencing and coordination lives here.
4. **Database access goes through the trading MCP server or `src/db/` directly.** No raw SQL scattered in agent or orchestrator code.
5. **Config via environment variables.** All API keys, DB URLs, and settings come from `.env` loaded by `src/config.py` using Pydantic Settings. Never hardcode secrets.

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
- `PnLReport` — profit/loss summary for a period

Define these in `src/db/models.py` and import everywhere. Do NOT use raw dicts for structured data.

## LLM Agent Guidelines

- Each LLM provider runs a 3-stage pipeline: Sentiment → Market → Trader.
- All stages inherit from `BaseAgent` and return typed Pydantic models.
- Claude uses `anthropic` SDK with tiered models: Haiku 4.5 (sentiment), Sonnet 4.5 (market), Opus 4.6 (trader).
- MiniMax uses `openai` SDK (OpenAI-compatible, custom `base_url`) with MiniMax 2.5 for all stages.
- `pipeline.py` orchestrates the 3 stages in sequence and is provider-agnostic.
- `providers/claude.py` and `providers/minimax.py` handle API specifics (auth, model selection, response parsing).
- Use structured output / JSON mode where available. Fall back to prompt-based JSON extraction with Pydantic validation.
- System prompts live in `src/agents/prompts/` as markdown files (one per stage), NOT as inline strings.

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

Key vars: `ANTHROPIC_API_KEY`, `MINIMAX_API_KEY`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `T212_API_KEY`, `DATABASE_URL`.

## Testing

- Use `pytest` with `pytest-asyncio` for async tests.
- Mock external APIs (Reddit, Yahoo Finance, Trading 212, LLMs) in tests — never hit real APIs in CI.
- Test files mirror source structure: `tests/test_agents/`, `tests/test_mcp_servers/`, etc.
- Focus tests on: agent output parsing, P&L calculations, rotation logic, portfolio tracking.

# Trading Bot — Project Specification

## Overview

An insider-signal-driven agentic trading system. MiniMax handles research analysis (stage 1); Claude Opus makes the final buy decisions (stage 2). One strategy runs per day on a single T212 demo (practice) account with a configurable EUR budget. Fully autonomous — no human approval, no database.

---

## High-Level Architecture

```
                       ┌──────────────────────┐
                       │     Supervisor        │
                       │  (scheduler + flow)   │
                       └──────────┬───────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │      Insider Digest          │
                    │  OpenInsider cluster buys    │
                    │  + parallel enrichment       │
                    │  → up to 25 candidates       │
                    └─────────────┬──────────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │  Stage 1: Research           │
                    │  MiniMax-M2.5 (no tools)     │
                    │  → ResearchReport            │
                    └─────────────┬──────────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │  Stage 2: Trader             │
                    │  Claude Opus 4.6 (no tools)  │
                    │  → DailyPicks → PickReview   │
                    └─────────────┬──────────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │  execute_with_fallback()     │
                    │  → T212 Demo Account         │
                    │  up to €1000/run             │
                    └────────────────────────────┘
```

---

## Signal Sources

All candidates come from **OpenInsider only**.

| Source | What it provides | Role |
|--------|-----------------|------|
| **OpenInsider** | Cluster insider buys (2+ execs buying same stock) or solo C-suite with ΔOwn ≥ 3% | Primary candidates — direct input to pipeline |
| **yfinance** | Returns, fundamentals, technicals, earnings, news | Per-candidate enrichment (pre-fetched by supervisor) |
| **NewsAPI** | Recent headlines per ticker | Enrichment (fallback to yfinance news if missing) |
| **FMP / yfinance** | Earnings calendar | Enrichment context |
| **OpenInsider history** | 30/60/90d buy counts, acceleration flag | Enrichment context per ticker |

No Reddit, no screener sourcing, no earnings calendar as a candidate source.

---

## LLM Pipeline (2 active stages)

| Stage | Agent | Provider | Model | Tools |
|-------|-------|----------|-------|-------|
| 1 — Research | `ResearchAgent` | MiniMax | MiniMax-M2.5 | None |
| 2 — Trader | `TraderAgent` | Claude | Opus 4.6 | None |
| ~~3 — Risk Review~~ | ~~`RiskReviewAgent`~~ | — | — | *(inactive — DailyPicks wrapped directly into PickReview)* |

`SentimentAgent` and `MarketAgent` exist in the codebase but are **not called**.

### Stage 1: Research (MiniMax-M2.5, no tools)
- **Input:** Enriched insider digest (supervisor pre-fetches all data, formatted as text)
- **Output:** `ResearchReport` — per-ticker pros/cons/catalyst, no BUY/SELL verdict
- Analyst role only — scores and notes passed to Claude as context

### Stage 2: Trader (Claude Opus 4.6, no tools)
- **Input:** ResearchReport + enriched digest + current T212 portfolio + budget
- **System prompt:** `trader_aggressive.md` (single prompt, no strategy split)
- **Output:** `DailyPicks` → wrapped into `PickReview` — ranked buy list with allocation % and reasoning

---

## Trade Execution (Fallback Logic)

After the pipeline produces a ranked buy list, `execute_with_fallback()` tries to spend the full budget:

```
For each ranked pick (top allocation first, up to max_picks_per_run):
  1. Check remaining budget (stop if < €1)
  2. Fetch current price via MCP market data
  3. Place market order on T212 demo account
  4. If success → add to recently_traded.json, log, continue
  5. If fail → log reason, try next pick
  6. Continue until budget spent or all picks exhausted
```

---

## Sell Strategy

**None.** There is no automated sell logic. Positions accumulate in the demo account indefinitely until manually closed. The `sell_recommendations` field in `DailyPicks` is informational only.

---

## Stock Variety (3-Day Blacklist)

To prevent buying the same ticker repeatedly:
- After a successful buy, ticker → `recently_traded.json` with today's date
- Any ticker bought within `recently_traded_days` (default 3) days is filtered before research
- Format: `{"NVDA": "2026-02-19", "MSFT": "2026-02-18", ...}`

---

## Daily Schedule (Europe/Berlin, weekdays only)

| Time  | Job | Description |
|-------|-----|-------------|
| 17:10 | Trade execution | Insider digest → pipeline → buy orders |
| 17:35 | EOD snapshot | Portfolio snapshot + daily MD report |

Times configurable via `SCHEDULER_EXECUTE_TIME` and `SCHEDULER_EOD_TIME`.

---

## Daily Report Format

Generated automatically at 17:35, saved to `reports/YYYY-MM-DD.md`.

---

## Project Structure

```
src/
├── mcp_servers/
│   ├── market_data/        # yfinance, NewsAPI, OpenInsider, FMP, earnings
│   └── trading/            # T212 client + portfolio helpers
├── agents/
│   ├── research_agent.py   # Stage 1 (MiniMax)
│   ├── trader_agent.py     # Stage 2 (Claude Opus)
│   ├── pipeline.py         # Orchestrates stages 1→2
│   ├── providers/          # claude.py, minimax.py
│   └── prompts/            # System prompts (markdown)
├── orchestrator/
│   ├── supervisor.py       # Main decision cycle
│   ├── scheduler.py        # APScheduler (2 cron jobs)
│   ├── trade_executor.py   # Fallback buy logic
│   └── rotation.py         # Trading day check
├── utils/
│   └── recently_traded.py  # 3-day blacklist JSON file
├── notifications/
│   └── telegram.py         # Optional Telegram alerts
├── reporting/
│   └── daily_report.py     # Markdown report generation
├── models.py               # All Pydantic models (no DB)
└── config.py               # Pydantic Settings from .env

scripts/
├── run_scheduler.py        # Start the 24/7 daemon
└── report.py               # View live portfolio P&L from T212
```

---

## API Keys Required

```env
ANTHROPIC_API_KEY=sk-ant-...        # Required — Claude Opus
MINIMAX_API_KEY=...                  # Required — research pipeline
T212_API_KEY=...                     # Required — demo/practice account
NEWS_API_KEY=...                     # Optional — falls back to yfinance news
FMP_API_KEY=...                      # Optional — falls back to yfinance
```

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.13+ |
| Package Manager | `uv` |
| MCP SDK | `mcp` (FastMCP) |
| LLM — Claude | `anthropic` SDK |
| LLM — MiniMax | `openai` SDK (OpenAI-compatible) |
| Market Data | `yfinance` |
| News | NewsAPI via `httpx` |
| Insider Data | OpenInsider scraped via `httpx` + `beautifulsoup4` |
| Broker | Trading 212 REST API via `httpx` (demo only) |
| Storage | No DB — T212 API live + `recently_traded.json` |
| Scheduling | `APScheduler` (AsyncIOScheduler) |
| Validation | `pydantic` v2 |
| Notifications | Telegram bot (optional, no-op if disabled) |
| Testing | `pytest` + `pytest-asyncio` |

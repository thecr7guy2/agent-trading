# Trading Bot — Project Specification

## Overview

A hybrid multi-LLM agentic trading system for global stocks with a soft preference for EU listings. MiniMax handles cheap data-gathering (stages 1-2); Claude makes the final buy decisions (stages 3-4). Two strategies run in parallel each day:

- **Conservative** — real money (~€10/day) via T212 live account
- **Aggressive** — practice money (~€500/day) via T212 demo account

The system is fully autonomous — no human approval, no database. All state comes from the T212 API live, plus a small JSON file that prevents buying the same stock two days in a row.

---

## High-Level Architecture

```
                       ┌──────────────────────┐
                       │     Supervisor        │
                       │  (scheduler + flow)   │
                       └──────────┬───────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │      Signal Digest          │
                    │  Reddit + Screener +        │
                    │  OpenInsider + Earnings     │
                    │  → up to 15 candidates      │
                    └─────────────┬──────────────┘
                                  │
              ┌───────────────────▼────────────────────┐
              │          Shared Research                 │
              │   Stage 1 (MiniMax) → Stage 2 (MiniMax) │
              │   Sentiment → Research with tools        │
              └───────────────────┬────────────────────┘
                                  │
              ┌───────────────────▼────────────────────┐
              │  Fan out to both strategies in parallel  │
              └───────┬──────────────────┬─────────────┘
                      │                  │
         ┌────────────▼──────┐  ┌────────▼──────────────┐
         │   Conservative    │  │    Aggressive           │
         │  Stage 3: Opus    │  │  Stage 3: Opus          │
         │  Stage 4: Sonnet  │  │  Stage 4: Sonnet        │
         │  → T212 Live      │  │  → T212 Demo            │
         │  €10 real money   │  │  €500 practice money    │
         └───────────────────┘  └───────────────────────┘
```

---

## Signal Sources

All signals flow into a candidate list (max 15 tickers). Multi-source tickers rank highest.

| Source | What it provides | Role |
|--------|-----------------|------|
| **yfinance screener** | Global top movers, gainers, actives | Candidate tickers (EU gets +10% soft bonus) |
| **OpenInsider** | Cluster insider buys (2+ execs buying same stock, $50K+) | Direct candidates — highest conviction signal |
| **Reddit RSS** | Mention counts + upvote-weighted sentiment | Supplementary — boosts multi-source tickers |
| **NewsAPI** | Recent headlines per ticker | Per-candidate enrichment (MiniMax reads during research) |
| **FMP / yfinance** | Analyst estimate revisions (up/down trend) | Per-candidate enrichment — earnings momentum |
| **yfinance calendar** | Upcoming earnings announcements | Candidate signal for near-term catalysts |

### Candidate selection priority

1. **Multi-source first** — confirmed by 2+ independent signals
2. **Screener slots** — top movers from global markets
3. **Earnings slots** — upcoming catalysts
4. **Insider slots** — cluster buy signals
5. **Reddit-only** — fills remaining slots

After selection, blacklisted tickers (bought within 3 days) are removed. Remaining candidates are enriched with news headlines in parallel before being handed to the pipeline.

---

## LLM Pipeline (4 stages, hybrid providers)

Stages 1-2 run **once** (shared research). Stages 3-4 **fan out** to both strategies simultaneously.

| Stage | Agent | Provider | Model | Tools |
|-------|-------|----------|-------|-------|
| 1 — Sentiment | `SentimentAgent` | MiniMax | MiniMax-M2.5 | None |
| 2 — Research | `ResearchAgent` | MiniMax | MiniMax-M2.5 | 8 market data tools, up to 10 rounds |
| 3 — Trader | `TraderAgent` | Claude | Opus 4.6 | None |
| 4 — Risk Review | `RiskReviewAgent` | Claude | Sonnet 4.6 | None |

### Stage 1: Sentiment (MiniMax-M2.5, no tools)
- **Input:** Full signal digest (15 candidates with all source data)
- **Output:** `SentimentReport` — ranked tickers with refined sentiment scores
- Filters noise, identifies which candidates are worth deep research

### Stage 2: Research (MiniMax-M2.5, agentic tool calling)
- **Input:** Sentiment report
- **Tools available:** `get_stock_price`, `get_fundamentals`, `get_technical_indicators`, `get_stock_history`, `get_news`, `get_earnings`, `get_earnings_calendar`, `search_stocks`, `get_analyst_revisions`
- The model decides which tools to call and in what order
- **Output:** `ResearchReport` — each ticker scored (fundamentals 0-10, technicals 0-10, risk 0-10) with pros/cons list, catalyst, news summary

### Stage 3: Trader (Claude Opus 4.6, no tools)
- **Input:** Sentiment report + Research report + current portfolio from T212 + daily budget
- **Output:** `DailyPicks` — ranked buy list with allocation % and reasoning per pick
- Claude reads the research evidence and makes the final conviction call

### Stage 4: Risk Review (Claude Sonnet 4.6, no tools)
- **Input:** Trader picks + research report + portfolio
- **Output:** `PickReview` — same as DailyPicks but can veto picks or adjust allocations
- Sanity check: catches overconcentration, excessive risk, picks that contradict the research

---

## Trade Execution (Fallback Logic)

After the pipeline produces a ranked buy list, `execute_with_fallback()` tries to spend the full budget:

```
For each ranked candidate (top conviction first):
  1. Check remaining budget (stop if < €1)
  2. Resolve ticker on T212 (some tickers unavailable on the platform)
  3. Place market order for available budget
  4. If success → add to blacklist (recently_traded.json), log, continue
  5. If fail → log reason, try next candidate
  6. Continue until budget spent or all candidates exhausted
```

This means if Claude's #1 pick isn't on T212, it automatically falls back to #2, #3, etc. — no wasted budget.

---

## Sell Strategy

The `SellStrategyEngine` evaluates all T212 positions 3x daily (09:30, 12:30, 16:45):

| Rule | Condition | Default |
|------|-----------|---------|
| Stop-loss | Return ≤ -X% | -10% |
| Take-profit | Return ≥ +X% | +15% |
| Hold-period | Days held ≥ N | 5 days |

Rules evaluated in priority order. Positions fetched live from T212 — no DB reads. Sells execute immediately via T212 API for both real and demo accounts.

---

## Stock Variety (3-Day Blacklist)

To prevent buying NVDA/MSFT every day:

- After a successful buy, the ticker is written to `recently_traded.json` with today's date
- Any ticker bought within the last `RECENTLY_TRADED_DAYS` (default: 3) days is filtered from candidates before research
- Format: `{"NVDA": "2026-02-19", "MSFT": "2026-02-18", ...}`

---

## Daily Schedule (Europe/Berlin, weekdays only)

| Time  | Job | Description |
|-------|-----|-------------|
| 08:00 | Reddit collection | Scrape RSS feeds |
| 09:30 | Sell check | Stop-loss / take-profit / hold-period |
| 12:00 | Reddit collection | Second round |
| 12:30 | Sell check | Mid-day evaluation |
| 16:30 | Reddit collection | Final round before market close |
| 16:45 | Sell check | Pre-close evaluation |
| 17:10 | Trade execution | Signal digest → pipelines → buy orders |
| 17:35 | EOD snapshot | Portfolio snapshot + daily MD report |

---

## Daily Report Format

Generated automatically at 17:35 and saved to `reports/YYYY-MM-DD.md`:

```markdown
# Trading Report — 2026-02-21 (Friday)

## Summary
- Conservative (Real): spent €9.50 / €10.00 — 2 stocks bought
- Practice (Demo): spent €487.30 / €500.00 — 4 stocks bought

## Today's Buys
### Conservative — Real Money
| Ticker | Company | Amount | Price | Signal Sources | Why Claude bought it |

### Practice — Demo Account
| Ticker | Company | Amount | Price | Signal Sources | Why Claude bought it |

## Skipped / Failed
| Ticker | Reason |

## Current Positions (Live from T212)
### Real Account
| Ticker | Bought at | Now | P&L | Days held |

## Sell Triggers (if any)
| Ticker | Type | Return | Reason |
```

---

## Project Structure

```
src/
├── mcp_servers/
│   ├── reddit/             # RSS scraping + sentiment
│   ├── market_data/        # yfinance, NewsAPI, OpenInsider, FMP, screener
│   └── trading/            # T212 client + portfolio helpers
├── agents/
│   ├── sentiment_agent.py  # Stage 1
│   ├── research_agent.py   # Stage 2 (tool calling)
│   ├── trader_agent.py     # Stage 3
│   ├── risk_agent.py       # Stage 4
│   ├── pipeline.py         # Orchestrates stages 1→4
│   ├── providers/          # claude.py, minimax.py
│   └── prompts/            # System prompts (markdown)
├── orchestrator/
│   ├── supervisor.py       # Main decision cycle
│   ├── scheduler.py        # APScheduler cron jobs
│   ├── sell_strategy.py    # Sell rule evaluation
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
├── run_sell_checks.py      # Manual sell check
└── report.py               # View live portfolio P&L from T212
```

---

## API Keys Required

```env
ANTHROPIC_API_KEY=sk-ant-...        # Required — Claude Opus/Sonnet
MINIMAX_API_KEY=...                  # Required — research pipeline
T212_API_KEY=...                     # Required — live account
T212_PRACTICE_API_KEY=...            # Optional — enables demo/aggressive strategy
NEWS_API_KEY=...                     # Optional — falls back to yfinance news
FMP_API_KEY=...                      # Optional — falls back to yfinance recommendations
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
| Reddit | RSS feeds via `feedparser` (no API key needed) |
| Market Data | `yfinance` |
| News | NewsAPI via `httpx` |
| Insider Data | OpenInsider scraped via `httpx` + `beautifulsoup4` |
| Broker | Trading 212 REST API via `httpx` |
| Storage | No DB — T212 API live + `recently_traded.json` |
| Scheduling | `APScheduler` (AsyncIOScheduler) |
| Validation | `pydantic` v2 |
| Notifications | Telegram bot (optional, no-op if disabled) |
| Testing | `pytest` + `pytest-asyncio` |

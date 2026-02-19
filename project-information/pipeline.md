# Trading Bot — Full Pipeline Documentation

## Overview

The system runs fully autonomously via a scheduler daemon (`scripts/run_scheduler.py`). Two LLMs (Claude and MiniMax) alternate daily as the "main trader" placing real trades (~10 EUR/day) via Trading 212, while the other makes virtual picks tracked in PostgreSQL.

---

## Phase 1: Data Collection (08:00, 12:00, 16:30)

The scheduler calls `supervisor.collect_reddit_round()` three times during the day. Each round:

1. Hits Reddit RSS feeds (no API key needed) for subreddits like r/wallstreetbets, r/investing
2. Extracts tickers from post titles/bodies using regex (`$ASML`, `SAP.DE`, etc.)
3. Scores sentiment per ticker (bullish/bearish word matching + upvote weighting)
4. Accumulates posts across all 3 rounds, deduplicating by post ID

This is just **collection** — no LLMs run yet. It's building a pool of raw social data.

**Key files:**
- `src/mcp_servers/reddit/` — RSS scraping, sentiment scoring, digest generation

---

## Phase 2: Signal Digest (17:10 — start of trade execution)

When `run_decision_cycle()` fires, the first thing it does is call `build_signal_digest()`, which merges **3 sources in parallel**:

```
Reddit digest ──────┐
                     │
EU Market Screener ──┼──▶ Merged candidate list (up to 25 tickers)
                     │
Earnings Calendar ───┘
```

### Source 1 — Reddit

Calls `get_daily_digest()` on the Reddit MCP server. Returns all tickers mentioned today with mention counts, sentiment scores, and top quotes. Filters out noise (ETFs, acronyms like "CEO", "IPO", indices).

### Source 2 — EU Screener

Calls `screen_eu_markets()` via Yahoo Finance's EquityQuery. Finds the day's gainers, losers, and most active stocks across AMS, PAR, GER, MIL, MCE, LSE exchanges. Minimum 1B market cap filter.

### Source 3 — Earnings Calendar

Calls `get_earnings_calendar()` to find stocks with upcoming earnings this week.

### Candidate Selection

The `_select_candidates()` function merges all sources and picks the top 25 with this priority:

1. **Multi-source tickers first** — confirmed by 2+ independent signals (highest value)
2. **~40% slots reserved for screener** — EU stocks are the trading target
3. **~10% for earnings** — upcoming catalysts
4. **Rest filled with Reddit-only tickers**

Finally, all 25 candidates are enriched with **news headlines** from Yahoo Finance in parallel.

**Key files:**
- `src/orchestrator/supervisor.py` — `build_signal_digest()`, `_select_candidates()`
- `src/mcp_servers/market_data/screener.py` — EU screener via yfinance EquityQuery
- `src/mcp_servers/market_data/finance.py` — news, earnings, fundamentals, technicals

---

## Phase 3: LLM Pipelines (17:10 — runs in parallel)

Two pipelines run **simultaneously** via `asyncio.gather()`:

```
Signal Digest ──▶ Claude Pipeline ──▶ Real trades (€10 budget)
             └──▶ MiniMax Pipeline ──▶ Virtual trades (tracked in DB)
```

Each pipeline runs **4 stages in sequence**:

### Stage 1: Sentiment (Haiku 4.5) — No tools

- **Input:** The full signal digest (25 candidates with Reddit sentiment, screener data, earnings, news)
- **Output:** `SentimentReport` — ranked tickers with refined sentiment scores
- **Purpose:** LLM filters and re-scores the raw data, identifies which tickers are actually worth researching

### Stage 2: Research (Sonnet 4.6) — 8 tools, up to 10 rounds

- **Input:** The sentiment report from Stage 1
- **This is where tool calling happens.** The LLM actively investigates each ticker by calling:
  - `get_stock_price` — current price, day change
  - `get_fundamentals` — P/E, EPS, margins, debt ratios
  - `get_technical_indicators` — RSI, MACD, Bollinger Bands, SMAs
  - `get_stock_history` — price chart over 7/30/90 days
  - `get_news` — recent headlines
  - `get_earnings` — upcoming earnings dates + estimates
  - `get_earnings_calendar` — broader earnings context
  - `search_eu_stocks` — search by name if needed
- The LLM decides which tools to call and in what order (agentic behavior)
- **Output:** `ResearchReport` — each ticker scored on fundamentals (0-10), technicals (0-10), risk (0-10), with a summary and catalyst

### Stage 3: Trader (Sonnet 4.6) — 2 tools, up to 5 rounds

- **Input:** Sentiment report + Research report + current portfolio + €10 budget
- **Tools:** `get_stock_price` (verify prices), `get_portfolio` (check existing positions)
- **Output:** `DailyPicks` — final buy/sell decisions with allocation percentages and reasoning
- The LLM decides how to split the €10 across picks (e.g., 60% ASML, 40% SAP)

### Stage 4: Risk Review (Haiku 4.5) — No tools

- **Input:** The trader's picks + research report + portfolio
- **Output:** `PickReview` — same as DailyPicks but can **veto** picks or adjust allocations
- **Purpose:** Sanity check — catches overconcentration, excessive risk, or picks that don't match the research

**Key files:**
- `src/agents/pipeline.py` — orchestrates stages 1-4 per LLM provider
- `src/agents/sentiment_agent.py` — Stage 1
- `src/agents/research_agent.py` — Stage 2 (tool-calling)
- `src/agents/trader_agent.py` — Stage 3 (tool-calling)
- `src/agents/risk_agent.py` — Stage 4
- `src/agents/providers/claude.py` — Claude API wrapper with prompt caching
- `src/agents/providers/minimax.py` — MiniMax API wrapper
- `src/agents/tools.py` — tool definitions (research + trader tools)
- `src/agents/tool_executor.py` — parallel tool execution via MCP clients
- `src/agents/prompts/` — per-stage system prompts (markdown files)

---

## Phase 4: Trade Execution (17:10 — after pipelines complete)

The supervisor determines who trades real vs virtual based on day-of-week rotation:

- **Main trader** (e.g., Claude on Monday): Picks go to Trading 212 API as real market orders
- **Virtual trader** (e.g., MiniMax on Monday): Picks recorded in PostgreSQL as simulated trades

For real trades:

1. Normalize allocations to 100% if they exceed it
2. For each buy pick: fetch live price → calculate quantity from allocation → call `place_buy_order` on Trading 212
3. Duplicate guard: won't buy the same ticker twice on the same day
4. Budget cap: €10/day total across all picks

**Key files:**
- `src/orchestrator/supervisor.py` — `_execute_real_trades()`, `_execute_virtual_trades()`
- `src/orchestrator/rotation.py` — who trades real/virtual each day
- `src/mcp_servers/trading/t212_client.py` — Trading 212 API client
- `src/mcp_servers/trading/portfolio.py` — `PortfolioManager` (DB operations)

---

## Phase 5: Sell Checks (09:30, 12:30, 16:45 — 3x daily)

Independently from buying, `run_sell_checks()` evaluates ALL open positions against 3 rules (in priority order):

1. **Stop-loss:** Return ≤ -10% → sell immediately
2. **Take-profit:** Return ≥ +15% → sell immediately
3. **Hold-period:** Held ≥ 5 days → sell (forces turnover)

Real positions sell via Trading 212 API. Virtual positions sell in the DB.

**Key files:**
- `src/orchestrator/sell_strategy.py` — `SellStrategyEngine`
- `src/orchestrator/supervisor.py` — `run_sell_checks()`, `_execute_sell_signal()`

---

## Phase 6: End of Day (17:35)

`run_end_of_day()` takes a portfolio snapshot:

1. Fetches current prices for all positions
2. Calculates total invested, total value, realized P&L, unrealized P&L
3. Saves snapshots for each LLM x real/virtual (4 snapshots total)
4. Generates a markdown report to `reports/YYYY-MM-DD.md`

**Key files:**
- `src/orchestrator/supervisor.py` — `run_end_of_day()`
- `src/reporting/daily_report.py` — markdown report generation
- `src/reporting/pnl.py` — P&L calculations

---

## Daily Schedule Summary (Europe/Berlin, weekdays only)

| Time  | Job                  | Description                                         |
|-------|----------------------|-----------------------------------------------------|
| 08:00 | Reddit collection    | Scrape RSS feeds, build sentiment summaries          |
| 09:30 | Sell check           | Evaluate stop-loss / take-profit / hold-period rules |
| 12:00 | Reddit collection    | Second collection round                              |
| 12:30 | Sell check           | Mid-day sell evaluation                              |
| 16:30 | Reddit collection    | Final collection before market close                 |
| 16:45 | Sell check           | Pre-close sell evaluation                            |
| 17:10 | Trade execution      | Signal digest → LLM pipelines → buy stocks           |
| 17:35 | EOD snapshot         | Portfolio snapshot + daily MD report generation       |

---

## Model Assignment

| Stage         | Model          | Tools | Purpose                          |
|---------------|----------------|-------|----------------------------------|
| Sentiment     | Haiku 4.5      | 0     | Fast, cheap sentiment filtering  |
| Research      | Sonnet 4.6     | 8     | Agentic deep-dive per ticker     |
| Trader        | Sonnet 4.6     | 2     | Final allocation decisions       |
| Risk Review   | Haiku 4.5      | 0     | Sanity check, veto bad picks     |

---

## Where New Data Sources Plug In

The **signal digest** (Phase 2) is where new data sources integrate. The `build_signal_digest()` method in `src/orchestrator/supervisor.py` merges all sources into a unified candidate list. Adding a new source means:

1. Fetching data from the new source (e.g., insider trading from BaFin)
2. Merging tickers into the `candidates` dict with a new source tag (e.g., `"insider"`)
3. Updating `_select_candidates()` to give the new source appropriate priority

A ticker that appears in multiple sources (e.g., Reddit sentiment + insider buying + screener) is ranked highest — multi-source confirmation is the strongest signal.

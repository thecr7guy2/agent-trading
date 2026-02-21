# Trading Bot — Full Pipeline Documentation

## Overview

The system runs fully autonomously via a scheduler daemon (`scripts/run_scheduler.py`). Two strategies (conservative real-money and aggressive practice) run each trading day using a shared 4-stage LLM pipeline. No database — all state is fetched live from the T212 API.

---

## Phase 1: Data Collection (08:00, 12:00, 16:30)

The scheduler calls `supervisor.collect_reddit_round()` three times during the day. Each round:

1. Hits Reddit RSS feeds (no API key needed) for subreddits like r/wallstreetbets, r/investing, r/stocks
2. Extracts tickers from post titles/bodies using regex (`$ASML`, `SAP.DE`, `NVDA`, etc.)
3. Scores sentiment per ticker (bullish/bearish word matching + upvote weighting)
4. Accumulates posts across all 3 rounds, deduplicating by post ID

This is **collection only** — no LLMs run yet. Building a pool of raw social data.

**Key files:**
- `src/mcp_servers/reddit/` — RSS scraping, sentiment scoring, digest generation

---

## Phase 2: Signal Digest (17:10 — start of trade execution)

When `run_decision_cycle()` fires, it calls `build_signal_digest()`, which merges **4 sources**:

```
Reddit digest ────────┐
                       │
Global Screener ───────┼──▶ Merged candidate list (up to 15 tickers)
                       │         (after 3-day blacklist filter)
Earnings Calendar ─────┤
                       │
OpenInsider buys ──────┘
        │
        └──▶ Per-candidate news enrichment (parallel)
```

### Source 1 — Reddit
`get_daily_digest()` on the Reddit MCP server. Returns all tickers mentioned today with mention counts, sentiment scores, and top quotes. Filters out noise (ETFs, acronyms like CEO/IPO, indices).

### Source 2 — Global Screener
`screen_global_markets()` via yfinance. Finds top movers and most active stocks globally. EU-listed stocks (`.DE`, `.PA`, `.AS`, `.L`, `.MI`, `.MC`) receive a soft +10% scoring bonus — they're preferred but not required.

### Source 3 — Earnings Calendar
`get_earnings_calendar()` finds stocks with upcoming earnings this week — catalysts for near-term moves.

### Source 4 — OpenInsider Cluster Buys
`get_insider_activity()` scrapes OpenInsider for recent open-market purchases ($50K+) where 2+ distinct insiders bought the same stock. Cluster buys are one of the strongest forward-looking signals — executives buying with their own money.

### Candidate Selection

`_select_candidates()` merges all sources and selects up to 15:

1. **Multi-source tickers first** — confirmed by 2+ independent signals (highest value)
2. **Screener slots** — top global movers
3. **Earnings slots** — upcoming catalysts
4. **Insider slots** — cluster buy signals
5. **Reddit-only** — fills remaining slots

### Blacklist Filter

After selection, `recently_traded.json` is checked. Any ticker bought within the last 3 days is removed — prevents buying NVDA/MSFT every single day.

### News Enrichment

All remaining candidates are enriched with recent news headlines (NewsAPI with yfinance fallback) in parallel before being handed to the pipeline.

**Key files:**
- `src/orchestrator/supervisor.py` — `build_signal_digest()`, `_select_candidates()`
- `src/mcp_servers/market_data/screener.py` — global screener via yfinance
- `src/mcp_servers/market_data/insider.py` — OpenInsider scraper
- `src/mcp_servers/market_data/news.py` — NewsAPI client
- `src/mcp_servers/market_data/finance.py` — news, earnings, fundamentals, technicals

---

## Phase 3: LLM Pipelines (17:10 — after signal digest)

Stages 1-2 run **once** (shared research). Then stages 3-4 **fan out** to both strategies in parallel via `asyncio.gather()`.

```
Signal Digest
     │
     ▼
Stage 1: Sentiment (MiniMax-M2.5) ──▶ SentimentReport
     │
     ▼
Stage 2: Research (MiniMax-M2.5 + tools) ──▶ ResearchReport
     │
     ├──▶ Stage 3+4: Conservative (Claude) ──▶ Real trades (€10)
     └──▶ Stage 3+4: Aggressive (Claude) ──▶ Practice trades (€500)
```

### Stage 1: Sentiment (MiniMax-M2.5) — No tools

- **Input:** Full signal digest (15 candidates with Reddit sentiment, screener data, earnings, news, insider data)
- **Output:** `SentimentReport` — ranked tickers with refined sentiment scores
- **Purpose:** LLM filters and re-scores the raw data, identifies which tickers are worth deep research

### Stage 2: Research (MiniMax-M2.5) — Up to 10 tool-calling rounds

The LLM actively investigates each promising ticker using:

| Tool | What it fetches |
|------|----------------|
| `get_stock_price` | Current price, day change, 52-week range |
| `get_fundamentals` | P/E, EPS, margins, debt ratios, market cap |
| `get_technical_indicators` | RSI, MACD, Bollinger Bands, SMAs/EMAs |
| `get_stock_history` | Historical OHLCV (7/30/90 days) |
| `get_news` | Recent headlines (NewsAPI or yfinance) |
| `get_earnings` | Upcoming earnings date + EPS estimates |
| `get_earnings_calendar` | Broader earnings context for the week |
| `search_stocks` | Search by name if ticker lookup fails |
| `get_analyst_revisions` | Upgrade/downgrade trend, EPS revision direction |

The LLM decides which tools to call and in what order — this is agentic behavior.

**Output:** `ResearchReport` — each ticker has:
- `fundamental_score` (0-10), `technical_score` (0-10), `risk_score` (0-10)
- `pros` and `cons` lists
- `catalyst` — the main reason to buy (or not)
- `news_summary` — what's happening right now
- `earnings_outlook` — upcoming catalyst if relevant

### Stage 3: Trader (Claude Opus 4.6) — No tools

- **Input:** Sentiment report + Research report + current T212 portfolio + daily budget
- Claude reads each ticker's research evidence (pros/cons, catalyst, news) — **not the raw scores**
- **Output:** `DailyPicks` — final buy/sell decisions with allocation % and conviction reasoning

Two instances run in parallel:
- Conservative: reads `prompts/trader_conservative.md`, budget = €10
- Aggressive: reads `prompts/trader_aggressive.md`, budget = €500

### Stage 4: Risk Review (Claude Sonnet 4.6) — No tools

- **Input:** Trader's picks + research + portfolio
- **Output:** `PickReview` — same as DailyPicks + can veto picks or adjust allocations
- Catches: overconcentration, excessive risk, picks that contradict the research
- Has `vetoed_tickers` list and `adjustments` list

**Key files:**
- `src/agents/pipeline.py` — orchestrates stages 1-4 per strategy
- `src/agents/sentiment_agent.py` — Stage 1
- `src/agents/research_agent.py` — Stage 2 (tool-calling)
- `src/agents/trader_agent.py` — Stage 3
- `src/agents/risk_agent.py` — Stage 4
- `src/agents/providers/claude.py` — Claude API wrapper with prompt caching
- `src/agents/providers/minimax.py` — MiniMax API wrapper
- `src/agents/prompts/` — system prompts (markdown files, one per stage)

---

## Phase 4: Trade Execution (17:10 — after pipelines complete)

`execute_with_fallback()` in `src/orchestrator/trade_executor.py` handles buying with a fallback loop:

```
For each ranked candidate (top conviction first):
  1. Check remaining budget (stop if < €1)
  2. Fetch available cash from T212 (cap budget to actual cash balance)
  3. Resolve T212 broker ticker (some tickers not available on platform)
  4. If not found → log "not tradable", try next
  5. Calculate quantity = remaining_budget / current_price
  6. Place market order
  7. If success → record ticker in recently_traded.json, continue
  8. If T212 error → log error, try next
```

Both strategies execute in parallel:
- **Conservative** → T212 live account (real money)
- **Aggressive** → T212 demo account (practice money)

**Key files:**
- `src/orchestrator/trade_executor.py` — `execute_with_fallback()`
- `src/mcp_servers/trading/t212_client.py` — T212 REST API client

---

## Phase 5: Sell Checks (09:30, 12:30, 16:45 — 3x daily)

`run_sell_checks()` evaluates ALL open T212 positions against 3 rules (in priority order):

1. **Stop-loss:** Return ≤ -10% → sell immediately
2. **Take-profit:** Return ≥ +15% → sell immediately
3. **Hold-period:** Held ≥ 5 days → sell (forces turnover)

How it works:
1. Fetch all positions live from T212 (both live and demo accounts)
2. Fetch current prices for all held tickers
3. `SellStrategyEngine.evaluate_positions()` applies the rules
4. Each triggered signal → `place_sell_order` on the appropriate T212 account
5. Results logged and included in EOD report

Hold-period uses T212's `openDate` field — no DB needed.

**Key files:**
- `src/orchestrator/sell_strategy.py` — `SellStrategyEngine`
- `src/orchestrator/supervisor.py` — `run_sell_checks()`
- `src/mcp_servers/trading/portfolio.py` — `get_live_positions()`, `get_demo_positions()`

---

## Phase 6: End of Day (17:35)

`run_end_of_day()` fetches both T212 accounts and generates a portfolio snapshot:

1. `get_live_positions(t212_live)` → real account positions
2. `get_demo_positions(t212_demo)` → practice account positions
3. Calculates total invested, total value, unrealized P&L per account
4. Passes snapshots + raw positions to `generate_daily_report()`
5. Report written to `reports/YYYY-MM-DD.md`

**Key files:**
- `src/orchestrator/supervisor.py` — `run_end_of_day()`
- `src/reporting/daily_report.py` — clean markdown report generation

---

## Daily Schedule Summary (Europe/Berlin, weekdays only)

| Time  | Job | Description |
|-------|-----|-------------|
| 08:00 | Reddit collection | Round 1 |
| 09:30 | Sell check | Stop-loss / take-profit / hold-period |
| 12:00 | Reddit collection | Round 2 |
| 12:30 | Sell check | Mid-day |
| 16:30 | Reddit collection | Round 3 (final before close) |
| 16:45 | Sell check | Pre-close |
| 17:10 | Trade execution | Signal digest → pipelines → buy |
| 17:35 | EOD snapshot | Portfolio snapshot + daily report |

---

## Model Assignment

| Stage | Model | Provider | Tools | Notes |
|-------|-------|----------|-------|-------|
| 1 — Sentiment | MiniMax-M2.5 | MiniMax | 0 | Fast, cheap, filters noise |
| 2 — Research | MiniMax-M2.5 | MiniMax | 9 | Agentic deep-dive per ticker |
| 3 — Trader | Opus 4.6 | Claude | 0 | Final allocation decisions |
| 4 — Risk Review | Sonnet 4.6 | Claude | 0 | Sanity check, veto bad picks |

---

## Where New Data Sources Plug In

The **signal digest** (Phase 2) is where new data sources integrate. `build_signal_digest()` in `supervisor.py` merges all sources into a unified candidate list. Adding a new source means:

1. Create or extend an MCP tool to fetch the data
2. Call the tool in `build_signal_digest()` and merge tickers into the `candidates` dict with a new source tag (e.g., `"insider"`, `"earnings"`)
3. Update `_select_candidates()` to give the new source appropriate priority

A ticker appearing in multiple sources (e.g., Reddit + insider buying + screener) ranks highest — multi-source confirmation is the strongest signal.

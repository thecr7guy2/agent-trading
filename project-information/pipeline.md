# Trading Bot — Full Pipeline Documentation

## Overview

The system runs fully autonomously via a scheduler daemon (`scripts/run_scheduler.py`). One strategy (demo/practice account) runs each trading day via a 2-stage LLM pipeline. No database — all state is fetched live from the T212 API.

---

## Daily Flow

```
17:10 — Supervisor.run_decision_cycle()
           │
           ▼
    build_insider_digest()
    ├── get_insider_candidates() — OpenInsider scrape
    └── _enrich_candidate() × N (parallel)
           ├── yfinance: returns 1m/6m/1y
           ├── yfinance: fundamentals (P/E, market cap, margins)
           ├── yfinance: technicals (RSI, MACD, Bollinger)
           ├── yfinance: earnings calendar
           ├── OpenInsider: ticker history (30/60/90d buy counts)
           └── NewsAPI (or yfinance fallback): recent headlines
           │
           ▼
    Blacklist filter (recently_traded.json — 3-day window)
           │
           ▼
    get_demo_positions() — T212 API (current portfolio)
           │
           ▼
    AgentPipeline.run()
    ├── Stage 1: ResearchAgent (MiniMax-M2.5) → ResearchReport
    └── Stage 2: TraderAgent (Claude Opus 4.6) → DailyPicks → PickReview
           │
           ▼
    execute_with_fallback() — T212 demo account buy orders
           │
           ▼
    Telegram notification (if enabled)

17:35 — Supervisor.run_end_of_day()
           ├── get_demo_positions() — snapshot
           └── generate_daily_report() → reports/YYYY-MM-DD.md
```

---

## Stage 1: Research (MiniMax-M2.5) — No tools

- **Input:** Enriched insider digest (up to 25 candidates, pre-formatted as text)
- **System prompt:** `src/agents/prompts/research.md`
- **Output:** `ResearchReport` — per-ticker `ResearchFinding` with:
  - `pros` list, `cons` list
  - `catalyst` — main thesis
  - `fundamental_score`, `technical_score`, `risk_score`
  - `news_summary`, `earnings_outlook`

MiniMax does **not** make any tool calls in this stage — all data was pre-fetched by the supervisor's parallel enrichment. The research prompt instructs it to be an analyst only (no BUY/SELL verdict).

---

## Stage 2: Trader (Claude Opus 4.6) — No tools

- **Input:** ResearchReport + enriched digest + current T212 portfolio + budget
- **System prompt:** `src/agents/prompts/trader_aggressive.md` (single prompt, hardcoded)
- **Output:** `DailyPicks` → wrapped into `PickReview` (Stage 3 / risk review is inactive)
  - `picks`: ranked list of `StockPick` (ticker, action, allocation_pct, reasoning, confidence)
  - `sell_recommendations`: any sell suggestions (informational only — no automated sell logic)
  - `confidence`: overall confidence score
  - `market_summary`: narrative

Claude reads the MiniMax analyst notes as context but forms its own independent buy thesis. It selects from the insider candidates and assigns allocation percentages summing to 100%.

---

## Trade Execution

`execute_with_fallback()` in `src/orchestrator/trade_executor.py`:

```
For each buy pick (ranked by allocation_pct, top first):
  1. Check remaining budget (stop if < €1)
  2. Fetch current price via MCP market data tool
  3. Place market order on T212 demo account
  4. If success → record ticker in recently_traded.json, continue
  5. If T212 error or ticker unavailable → log, try next pick
  6. Continue until budget spent or all picks exhausted
```

Max picks per run is capped by `max_picks_per_run` (default 5) before the loop starts.

---

## Insider Conviction Scoring

`get_insider_candidates()` in `src/mcp_servers/market_data/insider.py`:

Per transaction: `score = delta_own_pct × title_multiplier × recency_decay`
- `title_multiplier`: 3.0 for C-suite (CEO/CFO/COO/CTO/President/Chairman), 1.0 otherwise
- `recency_decay`: `e^(-0.2 × days_since_trade)` — fresher buys rank higher
- `delta_own_pct`: % stake increase; "New" positions = 100%

Candidates are grouped by ticker (scores summed). A candidate is **included only if**:
- Cluster buy: 2+ distinct insiders bought the same stock, OR
- Solo C-suite: 1 insider, is C-suite, ΔOwn ≥ 3%

Top `INSIDER_TOP_N` (default 25) by conviction score are passed to enrichment.

---

## Key Files

| File | Role |
|------|------|
| `src/orchestrator/supervisor.py` | Main orchestrator — digest + pipeline + execution |
| `src/orchestrator/scheduler.py` | APScheduler cron jobs (2 jobs only) |
| `src/orchestrator/trade_executor.py` | `execute_with_fallback()` |
| `src/agents/pipeline.py` | `AgentPipeline` — runs stages 1→2 |
| `src/agents/research_agent.py` | Stage 1: MiniMax analyst |
| `src/agents/trader_agent.py` | Stage 2: Claude Opus trader |
| `src/agents/providers/claude.py` | Claude API wrapper |
| `src/agents/providers/minimax.py` | MiniMax API wrapper (OpenAI-compatible) |
| `src/agents/prompts/research.md` | Stage 1 system prompt |
| `src/agents/prompts/trader_aggressive.md` | Stage 2 system prompt |
| `src/mcp_servers/market_data/insider.py` | OpenInsider scraper + conviction scoring |
| `src/mcp_servers/market_data/finance.py` | yfinance wrappers |
| `src/mcp_servers/market_data/news.py` | NewsAPI client |
| `src/mcp_servers/market_data/earnings.py` | FMP/yfinance earnings calendar |
| `src/mcp_servers/trading/t212_client.py` | T212 REST API client |
| `src/mcp_servers/trading/portfolio.py` | `get_demo_positions()` |
| `src/utils/recently_traded.py` | 3-day blacklist JSON helper |
| `src/reporting/daily_report.py` | Markdown daily report |

---

## Daily Schedule (Europe/Berlin, weekdays only)

| Time  | Job | Description |
|-------|-----|-------------|
| 17:10 | Trade execution | Insider digest → pipeline → buy orders |
| 17:35 | EOD snapshot | Portfolio snapshot + daily MD report |

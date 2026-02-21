# Trading Bot Overhaul Plan

## Goals
1. Remove all DB code — no SQLite, no migrations, no aiosqlite
2. Trade replacement: if an order fails, try the next candidate until budget is spent
3. Stock variety: stop buying NVDA/MSFT every day — 3-day blacklist after each buy
4. Better data sources: Reddit RSS (supplementary) + NewsAPI + OpenInsider + Earnings Revisions
5. EU stocks preferred but not forced — if a US stock is the best pick, take it
6. Clean readable daily reports (markdown, no noise)
7. MiniMax = research + tool calling | Claude = final buy decisions

---

## Phase 1 — Delete Dead Weight

**Delete these entirely:**
- `src/db/` — all DB connection, models, migration code
- `src/backtesting/` — needs DB, no longer viable without it
- `src/mcp_servers/market_data/bafin.py` — doesn't work, removing
- `scripts/setup_db.py`
- `scripts/backtest.py`
- `trading_bot.db`

**Remove from pyproject.toml:**
- `aiosqlite`
- `asyncpg` (already removed but verify)

---

## Phase 2 — Simplify Config

**Remove from `src/config.py`:**
- `sqlite_path`, `database_url`
- `bafin_lookback_days`
- `signal_candidate_limit`, `screener_min_market_cap`, `screener_exchanges`
- `t212_practice_api_key`, `t212_practice_api_secret` (consolidate — T212 demo uses same key, different base URL)

**Add to `src/config.py`:**
- `news_api_key: str = ""` — NewsAPI key (optional, skipped if empty)
- `fmp_api_key: str = ""` — Financial Modeling Prep key (optional, for earnings revisions)
- `recently_traded_path: str = "recently_traded.json"` — rolling blacklist file
- `recently_traded_days: int = 3` — blacklist duration after buying
- `max_candidates: int = 15` — how many ranked stocks the pipeline produces (for fallback tries)
- `eu_preference_bonus: float = 0.1` — soft scoring bonus for EU-listed stocks (10%), no hard exclusion

---

## Phase 3 — Stock Variety Fix

**Create `src/utils/recently_traded.py`:**
- Reads/writes `recently_traded.json`
- `add(ticker)` — adds ticker with today's date
- `get_blacklist()` — returns set of tickers bought within `recently_traded_days` (3 days)
- `cleanup()` — removes entries older than `recently_traded_days`
- File format: `{"NVDA": "2026-02-18", "MSFT": "2026-02-19", ...}`

**Update screener (`src/mcp_servers/market_data/screener.py`):**
- Screen globally — no hard exchange filter, no market cap exclusion
- EU stocks (.DE, .PA, .AS, .BR, .L, .MI) get a soft scoring bonus during candidate ranking
- The bonus nudges the model toward EU picks when quality is roughly equal, but a clearly
  better US stock will still win
- After screening, remove any ticker present in the 3-day blacklist

**Update `_select_candidates()` in supervisor.py:**
- Remove blacklisted tickers
- Pass up to `max_candidates` (15) to the pipeline so there are enough fallbacks

---

## Phase 4 — Data Sources

### What we keep
- **Reddit RSS** (`src/mcp_servers/reddit/`) — supplementary social sentiment signal
- **yfinance** — fundamentals, technicals, price history

### What we add

#### A. NewsAPI — `src/mcp_servers/market_data/news.py`
- `get_company_news(ticker, company_name)` — real news headlines per stock
- Free tier: 1000 requests/day, sufficient for 15 candidates
- Falls back silently if `news_api_key` is empty
- Endpoint: `https://newsapi.org/v2/everything?q={company_name}&language=en&sortBy=publishedAt&pageSize=5`

#### B. OpenInsider — `src/mcp_servers/market_data/insider.py`
- Scrapes OpenInsider.com (free, public, no API key needed)
- `get_recent_insider_buys(days=7)` — returns list of cluster buys (multiple insiders buying same stock)
- Filters to: purchase transactions only (not option exercises), minimum $50k value
- Why it matters: when executives buy their own stock with their own money, it's one of
  the most reliable forward-looking signals available. Cluster buys (multiple insiders at once)
  are even stronger.
- Endpoint: `https://openinsider.com/screener?s=&o=&pl=&ph=&ll=&lh=&fd=7&td=&tdr=&fdlyl=&fdlyh=&daysago=&xp=1&xs=1&vl=50&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999&grp=0&nfl=&nfh=&nil=&nih=&nol=&noh=&v2l=&v2h=&oc2l=&oc2h=&sortcol=0&cnt=20&action=1`
- These tickers feed directly into the candidate list — insider buying is a candidate signal,
  not just supplementary data

#### C. Earnings Revisions — `src/mcp_servers/market_data/earnings.py`
- Uses Financial Modeling Prep (FMP) free tier OR yfinance analyst data as fallback
- `get_earnings_revisions(ticker)` — returns recent analyst estimate changes
- Flag tickers where estimates have been revised upward in last 30 days
- Why it matters: "earnings momentum" — stocks with rising analyst estimates consistently
  outperform. Post-earnings-beat stocks often drift upward for weeks (PEAD effect).
- FMP free tier: 250 requests/day, enough for candidate enrichment
- Fallback if no `fmp_api_key`: use yfinance `.info["recommendationTrend"]` as proxy

### How signals combine in the pipeline

```
Candidate sources (all feed into the 15-ticker candidate list):
  - yfinance screener (top movers, gainers, actives)
  - OpenInsider cluster buys (direct candidates)
  - Reddit high-mention tickers (supplementary)

Per-candidate enrichment (MiniMax uses all of these via tools):
  - NewsAPI headlines (what's happening right now)
  - yfinance fundamentals + technicals (valuation, momentum)
  - Earnings revisions (analyst sentiment trend)
  - Reddit sentiment (social signal)
  - EU soft bonus (applied during candidate ranking)

Claude sees:
  - Consolidated research brief per ticker (no raw scores, just evidence)
  - Makes final buy/pass decision per ticker
  - Ranks picks by conviction
```

---

## Phase 5 — Trade Replacement Logic

**Create `src/orchestrator/trade_executor.py`:**

```python
async def execute_with_fallback(candidates, budget, is_real):
    spent = 0.0
    bought = []
    failed = []

    for candidate in candidates:
        if spent >= budget:
            break
        remaining = budget - spent
        result = await place_order(candidate.ticker, remaining, is_real)
        if result.success:
            spent += result.amount_spent
            bought.append(result)
        else:
            failed.append((candidate.ticker, result.error))

    return bought, failed
```

- `place_order` calls T212 live API (real) or T212 demo API (practice)
- If instrument not found, unavailable, or order rejected → log reason, try next candidate
- Continues until budget is spent or all 15 candidates exhausted
- Returns what was bought + what failed and why (both appear in the daily report)

---

## Phase 6 — Simplified Pipeline

**Updated `src/orchestrator/supervisor.py` flow:**

```
run_daily_pipeline()
  1. Collect Reddit RSS sentiment (background, runs at 08:00/12:00/16:30)
  2. At 17:10:
     a. Screen global markets for top movers (yfinance)
     b. Fetch OpenInsider cluster buys from last 7 days
     c. Merge into candidate list (max 15), remove 3-day blacklist tickers
     d. Apply EU soft bonus to EU-listed candidates in the list
     e. MiniMax researches each candidate (news + fundamentals + revisions + reddit)
     f. Claude reviews all research briefs → picks ranked buy list
     g. Risk review (Sonnet) — sanity check on Claude's picks
     h. execute_with_fallback() → place orders until budget spent
     i. Update recently_traded.json with successfully bought tickers
     j. Generate daily report
```

**Scheduler jobs (unchanged times):**
- 08:00, 12:00, 16:30 — Reddit RSS collection
- 09:30, 12:30, 16:45 — Sell checks (T212 live positions, no DB)
- 17:10 — Trade execution
- 17:35 — EOD report

---

## Phase 7 — Sell Strategy Without DB

**Update `src/orchestrator/sell_strategy.py`:**
- Pull positions live from T212 API (`/portfolio` endpoint)
- Stop-loss (-10%), take-profit (+15%), hold-period (5 days) — unchanged
- Hold-period: use T212 position `openDate` field (no DB needed)
- No reads or writes to any DB

---

## Phase 8 — Clean Reporting

**Rewrite `src/reporting/daily_report.py`:**

```markdown
# Trading Report — 2026-02-21

## Summary
- Conservative (Real): spent €9.50 / €10.00 — 2 stocks bought
- Practice (Demo): spent €487.30 / €500.00 — 4 stocks bought

## Today's Buys

### Conservative — Real Money
| Ticker | Company    | Amount | Price   | Signal sources         | Why Claude bought it          |
|--------|------------|--------|---------|------------------------|-------------------------------|
| SAP.DE | SAP SE     | €5.00  | €180.20 | Insider buy, News      | CFO bought €2M shares. Strong Q4. |
| AIR.PA | Airbus SE  | €4.50  | €162.10 | Earnings revision      | Analysts raised estimates 3x in 30d. |

### Practice — Demo Account
| Ticker | Company    | Amount | Price   | Signal sources         | Why Claude bought it          |
|--------|------------|--------|---------|------------------------|-------------------------------|
| ...    | ...        | ...    | ...     | ...                    | ...                           |

## Skipped / Failed
| Ticker | Reason                              |
|--------|-------------------------------------|
| NVDA   | Blacklisted (bought 2026-02-19)     |
| RWE.DE | Not available on T212               |
| MSFT   | Claude: insufficient conviction     |

## Current Positions (Live from T212)

### Real Account
| Ticker | Bought at | Now     | P&L    | Days held |
|--------|-----------|---------|--------|-----------|
| SAP.DE | €178.00   | €181.20 | +1.8%  | 1         |

### Practice Account
| Ticker | Bought at | Now     | P&L    | Days held |
|--------|-----------|---------|--------|-----------|
| ...    | ...       | ...     | ...    | ...       |
```

**What's removed vs before:**
- Signal source breakdowns / model divergence / Reddit score tables
- All DB-derived stats and leaderboard noise
- BAFIN section
- Anything that requires a DB query

---

## Phase 9 — Cleanup & Wiring

**`src/models.py`** (renamed from `src/db/models.py`, DB-specific fields removed):
- Keep: `StockPick`, `DailyPicks`, `Trade`, `Position`, `SellSignal`
- Remove: `SentimentReport` (replaced by per-ticker enrichment), `SignalSource`, all DB models

**`pyproject.toml`:**
- Remove: `aiosqlite`
- No new deps needed — OpenInsider via `httpx` (already a dep), FMP via `httpx`, NewsAPI via `httpx`

**`.env.example`:**
- Remove: `DATABASE_URL`, `SQLITE_PATH`, `BAFIN_*`
- Add: `NEWS_API_KEY` (optional), `FMP_API_KEY` (optional)

**`CLAUDE.md`:** Update to reflect new architecture.

---

## File Change Summary

| File / Directory | Action |
|---|---|
| `src/db/` | DELETE entirely |
| `src/backtesting/` | DELETE entirely |
| `src/mcp_servers/market_data/bafin.py` | DELETE |
| `scripts/setup_db.py` | DELETE |
| `scripts/backtest.py` | DELETE |
| `trading_bot.db` | DELETE |
| `src/utils/recently_traded.py` | CREATE — 3-day blacklist |
| `src/mcp_servers/market_data/news.py` | CREATE — NewsAPI |
| `src/mcp_servers/market_data/insider.py` | CREATE — OpenInsider scraper |
| `src/mcp_servers/market_data/earnings.py` | CREATE — earnings revisions (FMP/yfinance) |
| `src/orchestrator/trade_executor.py` | CREATE — buy with fallback |
| `src/models.py` | CREATE — slim models (no DB) |
| `src/config.py` | REWRITE |
| `src/orchestrator/supervisor.py` | REWRITE |
| `src/orchestrator/scheduler.py` | UPDATE |
| `src/orchestrator/sell_strategy.py` | UPDATE — T212 live, no DB |
| `src/mcp_servers/market_data/screener.py` | UPDATE — global screen + EU soft bonus |
| `src/mcp_servers/market_data/server.py` | UPDATE — add insider/news/earnings tools, remove BAFIN |
| `src/mcp_servers/trading/portfolio.py` | REWRITE — T212 only, no DB |
| `src/mcp_servers/trading/server.py` | UPDATE |
| `src/mcp_servers/reddit/` | KEEP — supplementary signal |
| `src/agents/pipeline.py` | UPDATE |
| `src/agents/prompts/research.md` | UPDATE — mention all signal sources |
| `src/reporting/daily_report.py` | REWRITE — clean format |
| `CLAUDE.md` | UPDATE |
| `.env.example` | UPDATE |

---

## Implementation Order

1. Phase 1 — Delete dead weight
2. Phase 2 — Simplify config
3. Phase 3 — Stock variety fix + EU soft preference
4. Phase 4 — New data sources (NewsAPI, OpenInsider, Earnings Revisions)
5. Phase 5 — Trade replacement / fallback executor
6. Phase 6 — Rewrite supervisor / pipeline flow
7. Phase 7 — Sell strategy without DB
8. Phase 8 — Clean reporting
9. Phase 9 — Models, wiring, env, CLAUDE.md

---

## API Keys Needed

| Service | Cost | Required? | What it unlocks |
|---|---|---|---|
| NewsAPI | Free (1000 req/day) | Recommended | Real news headlines per stock |
| OpenInsider | Free (no key) | Yes — use it | Insider cluster buys — high signal |
| FMP | Free (250 req/day) | Optional | Earnings revisions, analyst trends |
| yfinance | Free (no key) | Yes — already used | Fundamentals, technicals |

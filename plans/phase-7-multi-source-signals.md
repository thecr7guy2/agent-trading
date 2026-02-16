# Phase 7 — Multi-Source Signal Engine

## Problem

The entire stock universe is determined by what Reddit happens to mention on a given day. Both LLMs receive the **exact same Reddit digest → same 12 tickers → same market data**, so they converge on the same 2-3 "obvious" picks. On quiet Reddit days, the system may only surface 4-5 European tickers total. There is no independent stock discovery, no financial news, no earnings catalysts, and no way to find a stock that nobody on Reddit mentioned.

## Goals

1. Expand the daily candidate universe to **20-30 unique tickers** from multiple independent sources
2. Add **Yahoo Finance EU screener** (gainers/losers/most active per exchange) as the primary new discovery source
3. Add **per-ticker news enrichment** via `yf.Ticker.news` so LLMs see real catalysts, not just Reddit posts
4. Add **earnings calendar** to auto-surface stocks with upcoming earnings
5. Merge all sources into a unified **signal digest** with source attribution
6. Track source attribution in daily reports

## What Changed From the Previous Draft

The first version of this plan was over-engineered. After testing `yfinance 1.1.0`, here's what's real:

| Previous plan idea | Reality | Decision |
|-|-|-|
| News RSS MCP server (Reuters, Yahoo RSS, etc.) | RSS feeds are fragile — Reuters killed theirs, Yahoo's changes constantly | **Dropped.** Use `yf.Ticker.news` instead — 10 articles per ticker, reliable, zero new infrastructure |
| Predefined screeners (`day_gainers`, `most_actives`) | These are **US-only**. Tested: all results return `us_market` | **Replaced.** Use `EquityQuery` with per-exchange filters (AMS, PAR, GER, MIL, MCE, LSE) — tested, works, returns EU stocks |
| Weighted ranker formula (0.40/0.35/0.25) | Arbitrary numbers with no empirical basis. The LLMs ARE the ranking engine | **Dropped.** Simple union + dedup. Let Stage 1 LLM do the ranking — that's what it's for |
| 40 candidate limit | 40 tickers × 3 API calls = 120 yfinance calls. Slow, may hit rate limits | **Reduced to 25.** Plenty of diversity, manageable API load |
| Separate news MCP server | Unnecessary if news comes from yfinance per-ticker | **Dropped.** Extend existing market data MCP server |
| Feature flag for rollout | This is a personal project, not enterprise software | **Dropped.** Just build it right |

## Verified yfinance Capabilities (tested Feb 2026, v1.1.0)

### EquityQuery EU Screener — WORKS
```python
# Tested: returns real EU stocks, sortable by % change and volume
q = EQ('and', [
    EQ('gt', ['intradaymarketcap', 1_000_000_000]),
    EQ('eq', ['exchange', 'AMS'])
])
result = yf.screen(q, count=10, sortField='percentchange', sortAsc=False)
# → HEIJM.AS (+10.59%), BAMNB.AS (+4.62%), HAL.AS (+3.88%), ...
```
Supports exchanges: AMS (60 stocks >1B), PAR (157), GER (612), MIL (771), MCE (79), LSE (2074).
Sortable by: `percentchange`, `dayvolume`, `intradaymarketcap`.

### Per-Ticker News — WORKS
```python
t = yf.Ticker('SHELL.AS')
news = t.news  # → 10 articles with title, summary, provider, pubDate
# "Bernstein and RBC Capital Raise ASML Price Targets" [Insider Monkey]
# "Shell Q4 Revenue Beat Estimates" [Insider Monkey]
```
Returns structured content: title, summary (120+ chars), provider name, publication date.

### Earnings Calendar — WORKS
```python
# Global earnings this week
cal = yf.Calendars()
ec = cal.get_earnings_calendar()
# → WMT, PANW, MDT, LYG, CNH, etc. with EPS estimates

# Per-ticker earnings
t = yf.Ticker('ASML.AS')
t.calendar  # → {'Earnings Date': [date(2026, 4, 15)], 'Earnings Average': 6.647, ...}
```

## Architecture

### Data Flow (new)

```
BEFORE (Phase 6):
  Reddit RSS (11 subs) → 12 tickers → Market Data → LLM Pipeline

AFTER (Phase 7):
  Reddit RSS (11 subs)  ─────────────┐
  Yahoo EU Screener (6 exchanges) ───┼─→ Union + Dedup → 20-25 tickers
  Earnings Calendar ─────────────────┘         │
                                               ▼
                                    Market Data Fetch (price + fundamentals + technicals)
                                               │
                                    Per-Ticker News Enrichment (yf.Ticker.news)
                                               │
                                               ▼
                                    Signal Digest (tickers + sources + news + earnings)
                                               │
                                    ┌──────────┴──────────┐
                                    ▼                     ▼
                              Claude Pipeline       MiniMax Pipeline
                              (Sentiment→Market→    (Sentiment→Market→
                               Trader)               Trader)
```

### Key Design Decisions

**No deterministic ranker.** The LLMs are the ranking engine. Stage 1 receives all candidates with source attribution and evidence (Reddit posts, screener reason, news headlines, earnings dates). The LLM decides what matters. This is simpler, more flexible, and leverages what LLMs are actually good at.

**News is enrichment, not discovery.** Instead of a separate news RSS scraper that might extract 3 ticker mentions from 50 generic articles, we fetch news *per candidate ticker* after building the candidate list. Every candidate gets 5-10 relevant news articles. Much higher signal-to-noise.

**Screener runs once, not 3x daily.** EU market movers don't change drastically hour-to-hour. Screen once at the 16:30 collection round (closest to trade execution at 17:10). Reddit still collects 3x to accumulate posts.

**25 candidates, not 40.** 25 tickers × 3 market data calls = 75 yfinance API calls. Add 25 news fetches = 100 calls total. Manageable. 40 would be 160 calls and risk rate limiting.

## Scope

### In Scope
1. Yahoo Finance EU screener (top gainers, losers, most active — per exchange)
2. Per-ticker news enrichment via `yf.Ticker.news`
3. Earnings calendar integration (global via `Calendars` + per-ticker via `Ticker.calendar`)
4. Unified signal digest format with source attribution
5. Updated agent prompts for multi-source input
6. Daily report enhancements (source counts, model divergence)
7. DB migration for signal source tracking
8. Tests for all new modules

### Out of Scope
1. Separate news RSS MCP server (not needed — yfinance provides per-ticker news)
2. Deterministic weighted ranker (LLMs rank better)
3. Forced LLM pick overlap caps (artificial — let them diverge naturally)
4. Paid data providers
5. Feature flags / shadow mode (just build it)

## Implementation Plan

### Step 1: Config + Models + Migration

**Files:** `src/config.py`, `.env.example`, `src/db/models.py`, `src/db/migrations/003_signal_sources.sql`, `scripts/setup_db.py`

**Config additions:**
```python
# Multi-source signals
signal_candidate_limit: int = 25
screener_min_market_cap: int = 1_000_000_000  # 1B EUR
screener_exchanges: str = "AMS,PAR,GER,MIL,MCE,LSE"
```

**New models in `src/db/models.py`:**
```python
class SignalSource(str, Enum):
    REDDIT = "reddit"
    SCREENER = "screener"
    EARNINGS = "earnings"

class TickerSignal(BaseModel):
    """A single ticker from a single source."""
    ticker: str
    source: SignalSource
    reason: str                        # "day_gainer", "reddit_mention", "earnings_upcoming"
    score: float = 0.0                 # source-specific relevance (0-1)
    evidence: list[dict] = []          # posts, articles, screener metadata

class SignalDigest(BaseModel):
    """Merged multi-source digest fed to Stage 1."""
    date: str
    sources_collected: list[str]
    candidates: list[CandidateTicker]
    total_reddit_posts: int = 0
    subreddits_scraped: list[str] = []

class CandidateTicker(BaseModel):
    """A ticker with data from all sources that mentioned it."""
    ticker: str
    sources: list[TickerSignal]        # all sources that surfaced this ticker
    reddit_sentiment: float = 0.0      # -1 to 1, from Reddit if available
    reddit_mentions: int = 0
    reddit_posts: list[dict] = []      # top 3 posts
    news_headlines: list[dict] = []    # from yf.Ticker.news (title, summary, provider, date)
    screener_hit: str | None = None    # "day_gainer_AMS", "most_active_PAR", etc.
    screener_rank: int | None = None   # position in screener results
    screener_change_pct: float | None = None
    earnings_date: str | None = None   # upcoming earnings date if within 7 days
    earnings_estimate: float | None = None
```

**DB migration (`003_signal_sources.sql`):**
```sql
CREATE TABLE IF NOT EXISTS signal_sources (
    id SERIAL PRIMARY KEY,
    scrape_date DATE NOT NULL,
    ticker VARCHAR(20) NOT NULL,
    source VARCHAR(20) NOT NULL,
    reason VARCHAR(50),
    score NUMERIC(5,3),
    evidence_json JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scrape_date, ticker, source)
);

CREATE INDEX idx_signal_sources_date ON signal_sources(scrape_date);
```

### Step 2: Yahoo Finance EU Screener

**Files:** `src/mcp_servers/market_data/screener.py` (new), `src/mcp_servers/market_data/server.py` (extend)

**`screener.py` implementation:**
```python
EU_EXCHANGES = ["AMS", "PAR", "GER", "MIL", "MCE", "LSE"]

SCREEN_QUERIES = {
    "day_gainers": {"sortField": "percentchange", "sortAsc": False},
    "day_losers": {"sortField": "percentchange", "sortAsc": True},
    "most_active": {"sortField": "dayvolume", "sortAsc": False},
}

async def screen_eu_exchange(
    exchange: str,
    query_type: str,
    min_market_cap: int = 1_000_000_000,
    count: int = 5,
) -> list[dict]:
    """Screen a single EU exchange. Returns normalized ticker list."""
    def _fetch():
        q = EQ('and', [
            EQ('gt', ['intradaymarketcap', min_market_cap]),
            EQ('eq', ['exchange', exchange])
        ])
        sort_config = SCREEN_QUERIES[query_type]
        result = yf.screen(q, count=count, **sort_config)
        return [
            {
                "ticker": quote["symbol"],
                "name": quote.get("shortName", ""),
                "exchange": exchange,
                "query": query_type,
                "price": quote.get("regularMarketPrice"),
                "change_pct": quote.get("regularMarketChangePercent", 0),
                "volume": quote.get("regularMarketVolume", 0),
                "market_cap": quote.get("marketCap", 0),
            }
            for quote in result.get("quotes", [])
        ]
    return await asyncio.to_thread(_fetch)

async def screen_all_eu(
    exchanges: list[str] = EU_EXCHANGES,
    min_market_cap: int = 1_000_000_000,
    per_query_count: int = 5,
) -> list[dict]:
    """Screen all EU exchanges for gainers, losers, most active.
    Returns deduplicated list sorted by abs(change_pct)."""
    tasks = [
        screen_eu_exchange(exch, query, min_market_cap, per_query_count)
        for exch in exchanges
        for query in SCREEN_QUERIES
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    # Flatten, dedup by ticker, keep first occurrence (highest relevance)
    seen = set()
    candidates = []
    for result in results:
        if isinstance(result, Exception):
            continue
        for item in result:
            if item["ticker"] not in seen:
                seen.add(item["ticker"])
                candidates.append(item)
    return candidates
```

This produces **~90 raw candidates** (6 exchanges × 3 queries × 5 per query), deduped to roughly **40-60 unique tickers**. After merging with Reddit and applying the candidate limit, we keep the top 25.

**New MCP tools in `server.py`:**
- `screen_eu_markets()` → calls `screen_all_eu()`, returns list of screener hits
- `get_ticker_news(ticker: str)` → wraps `yf.Ticker(ticker).news`, returns parsed articles
- `get_earnings_calendar(days_ahead: int = 7)` → wraps `yf.Calendars().get_earnings_calendar()`
- `get_ticker_earnings(ticker: str)` → wraps `yf.Ticker(ticker).calendar`

### Step 3: News + Earnings Enrichment

**Files:** `src/mcp_servers/market_data/finance.py` (extend)

**Per-ticker news:**
```python
async def get_ticker_news(ticker: str, max_items: int = 5) -> list[dict]:
    def _fetch():
        t = yf.Ticker(ticker)
        articles = []
        for item in (t.news or [])[:max_items]:
            content = item.get("content", {})
            articles.append({
                "title": content.get("title", ""),
                "summary": (content.get("summary", "") or "")[:200],
                "provider": content.get("provider", {}).get("displayName", ""),
                "pub_date": content.get("pubDate", ""),
            })
        return articles
    return await asyncio.to_thread(_fetch)
```

**Earnings calendar:**
```python
async def get_earnings_calendar_upcoming(days_ahead: int = 7) -> list[dict]:
    def _fetch():
        cal = yf.Calendars()
        df = cal.get_earnings_calendar()
        results = []
        for symbol, row in df.iterrows():
            results.append({
                "ticker": symbol,
                "company": row.get("Company", ""),
                "event": row.get("Event Name", ""),
                "date": str(row.get("Event Start Date", "")),
                "eps_estimate": row.get("EPS Estimate"),
                "market_cap": row.get("Marketcap"),
            })
        return results
    return await asyncio.to_thread(_fetch)

async def get_ticker_earnings(ticker: str) -> dict | None:
    def _fetch():
        t = yf.Ticker(ticker)
        cal = t.calendar
        if not cal:
            return None
        earnings_dates = cal.get("Earnings Date", [])
        return {
            "ticker": ticker,
            "earnings_date": str(earnings_dates[0]) if earnings_dates else None,
            "eps_estimate": cal.get("Earnings Average"),
            "revenue_estimate": cal.get("Revenue Average"),
        }
    return await asyncio.to_thread(_fetch)
```

### Step 4: Signal Digest Builder in Supervisor

**Files:** `src/orchestrator/supervisor.py` (extend), `src/orchestrator/mcp_client.py` (extend)

This is the core change. Replace the current flow:

```python
# OLD: Reddit-only
digest = await self.build_reddit_digest()
market_data = await self.build_market_data(digest)

# NEW: Multi-source
signal_digest = await self.build_signal_digest()
market_data = await self.build_market_data_from_signals(signal_digest)
```

**`build_signal_digest()` implementation:**
```python
async def build_signal_digest(self) -> dict:
    """Build unified signal digest from all sources."""
    # 1. Get Reddit digest (already collected via 3x daily rounds)
    reddit_digest = await self.build_reddit_digest()

    # 2. Get EU screener results (parallel across exchanges)
    screener_results = await self._market_data_client.call_tool(
        "screen_eu_markets", {}
    )

    # 3. Get upcoming earnings
    earnings = await self._market_data_client.call_tool(
        "get_earnings_calendar", {}
    )

    # 4. Build candidate set: union of all tickers
    candidates = {}  # ticker -> CandidateTicker data

    # Add Reddit tickers
    for t in reddit_digest.get("tickers", []):
        ticker = t["ticker"]
        candidates[ticker] = {
            "ticker": ticker,
            "sources": ["reddit"],
            "reddit_sentiment": t.get("sentiment_score", 0),
            "reddit_mentions": t.get("mentions", 0),
            "reddit_posts": t.get("top_posts", []),
        }

    # Add screener tickers
    for s in screener_results:
        ticker = s["ticker"]
        if ticker in candidates:
            candidates[ticker]["sources"].append("screener")
        else:
            candidates[ticker] = {
                "ticker": ticker,
                "sources": ["screener"],
                "reddit_sentiment": 0,
                "reddit_mentions": 0,
                "reddit_posts": [],
            }
        candidates[ticker]["screener_hit"] = s["query"]
        candidates[ticker]["screener_change_pct"] = s["change_pct"]
        candidates[ticker]["screener_rank"] = s.get("rank")

    # Add earnings tickers
    earnings_map = {e["ticker"]: e for e in earnings}
    for ticker, data in earnings_map.items():
        if ticker in candidates:
            candidates[ticker]["sources"].append("earnings")
        else:
            candidates[ticker] = {
                "ticker": ticker,
                "sources": ["earnings"],
                "reddit_sentiment": 0,
                "reddit_mentions": 0,
                "reddit_posts": [],
            }
        candidates[ticker]["earnings_date"] = data.get("date")
        candidates[ticker]["earnings_estimate"] = data.get("eps_estimate")

    # 5. Sort: multi-source tickers first, then by reddit mentions, then by screener rank
    sorted_candidates = sorted(
        candidates.values(),
        key=lambda c: (len(c["sources"]), c["reddit_mentions"]),
        reverse=True,
    )

    # 6. Cap at signal_candidate_limit
    top = sorted_candidates[:self._settings.signal_candidate_limit]

    # 7. Enrich top candidates with news headlines (parallel)
    async def _fetch_news(candidate):
        try:
            news = await asyncio.wait_for(
                self._market_data_client.call_tool(
                    "get_ticker_news", {"ticker": candidate["ticker"]}
                ),
                timeout=10.0,
            )
            candidate["news_headlines"] = news
        except Exception:
            candidate["news_headlines"] = []
        return candidate

    enriched = await asyncio.gather(
        *(_fetch_news(c) for c in top),
        return_exceptions=True,
    )

    final_candidates = [
        c for c in enriched if not isinstance(c, Exception)
    ]

    return {
        "date": reddit_digest.get("date", ""),
        "sources_collected": ["reddit", "screener", "earnings"],
        "total_candidates": len(final_candidates),
        "total_reddit_posts": reddit_digest.get("total_posts", 0),
        "subreddits_scraped": reddit_digest.get("subreddits_scraped", []),
        "candidates": final_candidates,
    }
```

**`build_market_data` updated** to accept the new digest format:
```python
async def build_market_data(self, digest: dict) -> dict[str, dict]:
    # Support both old format (digest["tickers"]) and new (digest["candidates"])
    if "candidates" in digest:
        tickers = [c["ticker"] for c in digest["candidates"]]
    else:
        tickers = [t["ticker"] for t in digest.get("tickers", [])]
    tickers = tickers[:self._settings.signal_candidate_limit]
    # ... rest unchanged
```

### Step 5: Update Pipeline + Prompts

**Files:** `src/agents/pipeline.py`, `src/agents/sentiment_agent.py`, `src/agents/prompts/sentiment.md`, `src/agents/prompts/market_analysis.md`, `src/agents/prompts/trader.md`

**`pipeline.py` change:**
```python
async def run(
    self,
    signal_digest: dict,          # new name
    market_data: dict,
    portfolio: list,
    budget_eur: float = 10.0,
    run_date: date | None = None,
    reddit_digest: dict | None = None,  # backward compat alias
) -> DailyPicks:
    digest = signal_digest or reddit_digest
    sentiment = await self._sentiment.run(digest)
    # ... rest unchanged
```

**`sentiment.md` updated prompt:**
```markdown
You are a signal analyst specializing in European stock markets.

## Your Task

Analyze the multi-source signal digest and produce a structured sentiment
report for each stock ticker. You receive data from multiple sources — use
them all to form a complete picture.

## Input

You will receive a signal digest containing candidates from multiple sources:

- **Reddit posts**: Titles and text from investing subreddits with pre-computed
  sentiment scores and mention counts. Good for retail sentiment and hype detection.
- **Screener hits**: Stocks flagged as top gainers, losers, or most active on EU
  exchanges today. These represent real price action — pay attention to WHY a stock
  is moving.
- **News headlines**: Recent financial news articles per ticker (title, summary,
  publisher). Use these to identify catalysts — earnings beats, analyst upgrades,
  M&A, regulatory changes.
- **Earnings calendar**: Tickers with upcoming earnings reports. Stocks approaching
  earnings often see increased volatility and opportunity.

Each candidate includes which sources surfaced it. A ticker appearing in multiple
sources (e.g., Reddit buzz + screener gainer + positive news) is a stronger signal
than one from a single source.

## What To Do

1. **Evaluate each candidate** across all available evidence
2. **Score sentiment** from -1.0 (very bearish) to 1.0 (very bullish):
   - Reddit sentiment gives you retail mood
   - News headlines reveal institutional/analyst views
   - Screener data shows what the market is actually doing (price > opinion)
3. **Rank by conviction**: Consider source agreement, evidence quality, catalyst strength
4. **Extract key quotes**: Best Reddit quotes AND most relevant news headlines
5. **Flag catalysts**: Earnings dates, analyst actions, sector moves

## Guidelines

- A screener gainer with no news or Reddit coverage might be a technical move — flag it
  but score conservatively unless you see a catalyst
- A Reddit-hyped stock that's actually a screener loser is a red flag — note the divergence
- Earnings within 3 days = heightened attention, not automatic bullish signal
- Weight news from established outlets (WSJ, Reuters, Bloomberg) over blog posts
- If a ticker has thin evidence from only one source, include it but note low conviction

## Output Format

Respond with a JSON object matching this exact schema:

{same SentimentReport schema as before — no changes}
```

**`market_analysis.md` additions:**
```markdown
## Input (updated)

You will receive:
1. **Sentiment Report**: Tickers ranked by multi-source sentiment
   (Reddit + news + screener-based scoring, not Reddit-only)
2. **Market Data**: For each ticker — current price, fundamentals, technicals

## Guidelines (additions)

- Cross-reference sentiment sources: if a ticker was flagged by the screener
  as a gainer AND has positive news catalysts, the technical setup matters more
- Stocks with upcoming earnings in 1-3 days carry extra event risk — factor
  this into the risk score
- Screener losers with strong fundamentals may be oversold bounce candidates
```

**`trader.md` additions:**
```markdown
## Guidelines (additions)

- **Source diversity matters**: A ticker surfaced by 3 sources (Reddit + screener
  + news) has stronger conviction than a single-source ticker
- **Catalyst-driven picks**: Stocks with clear news catalysts (earnings beat,
  analyst upgrade, sector rotation) are preferable to pure sentiment plays
- **Don't ignore screener-only tickers**: A stock that's a top EU gainer today
  with no Reddit coverage may be an opportunity the crowd hasn't found yet
```

### Step 6: Update Scheduler

**Files:** `src/orchestrator/scheduler.py`

Change the 16:30 collection round to also trigger screener + earnings fetch.
The `run_decision_cycle()` method already calls `build_signal_digest()` which handles everything.

```python
# Collection rounds at 08:00, 12:00 — Reddit only (screener not useful yet)
# Collection round at 16:30 — Reddit + screener + earnings (final round before trade)
# Trade execution at 17:10 — calls build_signal_digest() which merges all
```

Updated schedule:
| Time  | Job                     | What runs                                      |
|-------|-------------------------|-------------------------------------------------|
| 08:00 | Reddit collection       | Reddit RSS only (accumulate posts)              |
| 09:30 | Sell check              | Unchanged                                       |
| 12:00 | Reddit collection       | Reddit RSS only (accumulate posts)              |
| 12:30 | Sell check              | Unchanged                                       |
| 16:30 | Reddit collection       | Reddit RSS only (final accumulation)             |
| 16:45 | Sell check              | Unchanged                                       |
| 17:10 | Trade execution         | `build_signal_digest()` runs screener + earnings + merges with Reddit, then LLM pipelines |
| 17:35 | EOD snapshot            | Extended with source metrics in report           |

Screener + earnings + news enrichment all happen inside `build_signal_digest()` at 17:10.
No need to pre-fetch — the data is real-time from Yahoo Finance at execution time.

### Step 7: Update Daily Report

**Files:** `src/reporting/daily_report.py`

Add new sections to the daily markdown report:

```markdown
## Signal Sources
- Reddit: 15 tickers from 250 posts across 11 subreddits
- Screener: 22 tickers (gainers: 8, losers: 7, most active: 7) across 6 EU exchanges
- Earnings: 2 tickers with upcoming reports this week
- **Total unique candidates: 28** (after dedup and limit)

## Candidate Pool
| # | Ticker | Sources | Reddit Sent. | Screener | News Headlines | Earnings |
|---|--------|---------|-------------|----------|----------------|----------|
| 1 | ASML.AS | reddit, screener | +0.72 | gainer +3.8% | "Bernstein raises PT" | — |
| 2 | DSY.PA | screener | — | loser -10.4% | "Q4 miss on cloud" | — |
| 3 | SAP.DE | reddit | +0.45 | — | "Strong cloud growth" | Feb 20 |

## Model Divergence
- Claude picks: ASML.AS, SAP.DE, AIR.PA
- MiniMax picks: ASML.AS, DSY.PA, AD.AS
- Shared: 1 (ASML.AS)
- Unique to Claude: SAP.DE, AIR.PA
- Unique to MiniMax: DSY.PA, AD.AS
```

### Step 8: Persist Signal Sources

**Files:** `src/orchestrator/supervisor.py`, `src/mcp_servers/trading/portfolio.py`

After trade execution in `run_decision_cycle()`, persist each candidate's source data to the `signal_sources` table. Continue writing to `reddit_sentiment` table for backtest backward compat.

```python
async def _persist_signals(self, digest: dict, run_date: date) -> None:
    pm = await self._get_portfolio_manager()
    for candidate in digest.get("candidates", []):
        for source in candidate.get("sources", []):
            await pm.save_signal_source(
                scrape_date=run_date,
                ticker=candidate["ticker"],
                source=source,
                reason=candidate.get("screener_hit", "mention"),
                score=candidate.get("reddit_sentiment", 0),
                evidence={
                    "reddit_posts": candidate.get("reddit_posts", []),
                    "news": candidate.get("news_headlines", []),
                    "screener_change": candidate.get("screener_change_pct"),
                    "earnings_date": candidate.get("earnings_date"),
                },
            )
    # Backward compat: also persist to reddit_sentiment
    await self._persist_sentiment(digest, run_date)
```

### Step 9: Tests

**New test files:**
- `tests/test_mcp_servers/test_screener.py`
  - `screen_eu_exchange` returns correct format for each exchange
  - `screen_all_eu` deduplicates tickers across exchanges
  - Graceful handling when an exchange query fails
  - Correct sorting by change_pct and volume
  - Min market cap filter excludes small caps
- `tests/test_mcp_servers/test_ticker_news.py`
  - `get_ticker_news` parses yfinance news content structure
  - Handles tickers with no news gracefully
  - Truncates summary to max length
- `tests/test_mcp_servers/test_earnings.py`
  - `get_earnings_calendar_upcoming` returns parsed DataFrame
  - `get_ticker_earnings` returns calendar data
  - Handles tickers with no earnings data
- `tests/test_orchestrator/test_signal_digest.py`
  - Reddit + screener + earnings merge produces correct candidate list
  - Candidates from multiple sources sort above single-source
  - Candidate limit applied correctly
  - News enrichment runs in parallel and handles timeouts
  - Digest format backward-compatible (has "tickers" key or "candidates" key)

**Updated test files:**
- `tests/test_orchestrator/test_supervisor.py` — add `signal_candidate_limit` to SimpleNamespace fixtures
- `tests/test_orchestrator/test_scheduler.py` — add `signal_candidate_limit`, `screener_exchanges` to fixtures
- `tests/test_agents/test_pipeline.py` — verify `signal_digest` parameter accepted

## File Summary

### New Files
```
src/mcp_servers/market_data/screener.py
src/db/migrations/003_signal_sources.sql
tests/test_mcp_servers/test_screener.py
tests/test_mcp_servers/test_ticker_news.py
tests/test_mcp_servers/test_earnings.py
tests/test_orchestrator/test_signal_digest.py
```

### Modified Files
```
src/config.py
.env.example
src/db/models.py
src/mcp_servers/market_data/finance.py
src/mcp_servers/market_data/server.py
src/orchestrator/supervisor.py
src/orchestrator/scheduler.py
src/agents/pipeline.py
src/agents/sentiment_agent.py
src/agents/prompts/sentiment.md
src/agents/prompts/market_analysis.md
src/agents/prompts/trader.md
src/reporting/daily_report.py
src/mcp_servers/trading/portfolio.py
scripts/setup_db.py
tests/test_orchestrator/test_supervisor.py
tests/test_orchestrator/test_scheduler.py
```

## Risks and Mitigations

1. **yfinance screener rate limiting**
   - Mitigation: 6 exchanges × 3 queries = 18 screener calls. Well within limits. Add 1s delay between exchange batches if needed.

2. **Per-ticker news fetch is slow for 25 tickers**
   - Mitigation: `asyncio.gather()` with 10s timeout per ticker. Failed fetches get empty news — not a blocker.

3. **Screener returns tickers with non-EU suffixes** (e.g., ADRs on Frankfurt like `ZZA.F`)
   - Mitigation: Post-filter screener results to only keep `.AS`, `.PA`, `.DE`, `.MI`, `.MC`, `.L` suffixes. Reject `.F` (Frankfurt OTC/ADRs), `.SG` (Stuttgart), etc.

4. **Earnings calendar is US-heavy**
   - Mitigation: This is a bonus signal, not primary discovery. Even US earnings (like Walmart) can move correlated EU stocks. Also check per-ticker earnings for each EU candidate.

5. **More candidates = more LLM input tokens = higher cost**
   - Mitigation: 25 candidates vs 12 is ~2x the Stage 1 input. With Haiku for Stage 1, cost increase is negligible (~$0.01/day). News summaries are capped at 200 chars each.

6. **Backward compat with backtest engine**
   - Mitigation: Keep writing to `reddit_sentiment`. New `signal_sources` is additive. Backtest can be upgraded in a future phase.

## Acceptance Criteria

1. Daily candidate pool has 20+ unique tickers on normal market days (up from ~5-12)
2. At least 2 independent sources contribute candidates (Reddit + screener)
3. Each candidate includes news headlines when available
4. Daily report shows source attribution and model divergence
5. All existing tests pass without modification
6. New tests cover screener, news, earnings, and signal digest builder
7. Graceful degradation: if screener fails, falls back to Reddit-only
8. No paid API dependencies

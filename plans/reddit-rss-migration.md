# Reddit MCP Server: Switch from API to RSS Feeds

## Context

Reddit has made API access extremely restrictive. The current Reddit MCP server uses `asyncpraw` which requires `client_id` and `client_secret`. We're replacing it with **RSS feeds** — no authentication needed. Additionally, we want to **collect posts multiple times** throughout the day (at least 3 rounds) before making a buy decision at market close, and **expand the subreddit list** for better coverage.

## What Changes

| File | Action |
|------|--------|
| `pyproject.toml` | Remove `asyncpraw`, add `feedparser` |
| `src/config.py` | Make `reddit_client_id` and `reddit_client_secret` optional (`str \| None = None`) |
| `src/mcp_servers/reddit/scraper.py` | Replace `RedditScraper` class with `RSSCollector` class. Keep all pure functions (`extract_tickers`, `score_sentiment`, constants) |
| `src/mcp_servers/reddit/server.py` | Replace 4 old tools with 4 new tools |
| `tests/test_mcp_servers/test_reddit.py` | Rewrite scraper tests; keep pure function tests unchanged |
| `tests/test_config.py` | Update for optional Reddit credentials |
| `.env.example` | Mark Reddit API keys as optional |

## What Does NOT Change

- `extract_tickers()`, `score_sentiment()`, TICKER_PATTERN, TICKER_BLACKLIST, BULLISH/BEARISH_KEYWORDS — all pure functions, no API coupling
- `src/agents/sentiment_agent.py` — receives same dict format
- `src/agents/pipeline.py` — calls `sentiment.run(reddit_digest)` with same structure
- `src/agents/prompts/sentiment.md` — same digest format
- All other MCP servers, agents, DB models

## Step 1: Dependencies

**`pyproject.toml`** — swap `asyncpraw>=7.8` for `feedparser>=6.0`. Run `uv sync`.

## Step 2: Config

**`src/config.py`** — change:
```python
reddit_client_id: str | None = None
reddit_client_secret: str | None = None
```

**`tests/test_config.py`** — remove Reddit keys from required env vars in tests, add test confirming they're optional.

## Step 3: Scraper Rewrite

**`src/mcp_servers/reddit/scraper.py`**

**Keep unchanged (top of file):**
- `DEFAULT_SUBREDDITS` (but expand — see below)
- `TICKER_PATTERN`, `TICKER_BLACKLIST`, `BULLISH_KEYWORDS`, `BEARISH_KEYWORDS`
- `extract_tickers()`, `score_sentiment()`

**Expand subreddits** from 5 to 11:
```python
DEFAULT_SUBREDDITS = [
    "wallstreetbets", "investing", "stocks",
    "EuropeanStocks", "Euronext", "eupersonalfinance",
    "SecurityAnalysis", "stockmarket",
    "ValueInvesting", "dividends", "options",
]
```

**New `RedditPost` dataclass:**
```python
@dataclass
class RedditPost:
    id: str
    title: str
    body: str           # HTML stripped to plain text
    author: str
    subreddit: str
    url: str
    published: datetime
```

**New `RSSCollector` class** (replaces `RedditScraper`):
- `__init__(user_agent)` — no API keys needed
- `_posts: dict[str, RedditPost]` — in-memory accumulator, keyed by post ID for dedup
- `_collection_rounds: int` — tracks how many collection rounds completed
- `_fetch_feed(subreddit, sort)` — fetch one RSS feed via `httpx` + parse with `feedparser`
- `collect(subreddits=None)` — fetch hot/new/top feeds for all subreddits, deduplicate, return stats. Uses `asyncio.gather()` with `Semaphore(5)` for concurrent fetching + `asyncio.sleep(0.2)` rate limiting
- `get_daily_digest(subreddits=None)` — build digest from accumulated posts. **Same output format** as current: `{date, subreddits_scraped, total_posts, tickers: [{ticker, mentions, sentiment_score, top_posts}]}`
- `get_collection_stats()` — rounds completed, total posts, per-subreddit counts
- `reset()` — clear state for new day

**RSS feed URLs:** `https://www.reddit.com/r/{subreddit}/{sort}.rss` where sort = hot, new, top

**Key details:**
- 3 sorts per subreddit = ~75 entries per sub. 11 subreddits = ~825 per round. 3 rounds/day = 1000+ unique posts.
- No upvote data in RSS, so `score_sentiment()` called with default `upvotes=1`. The LLM agent compensates with qualitative analysis.
- `feedparser` normalizes Atom/RSS differences. HTML content stripped with regex + `html.unescape()`.
- Post ID extracted from Atom `<id>` field (format: `t3_abc123`) or URL fallback.

## Step 4: Server Rewrite

**`src/mcp_servers/reddit/server.py`** — replace tools:

| Old Tool | New Tool | Notes |
|----------|----------|-------|
| `search_subreddit` | *removed* | RSS doesn't support search |
| `get_trending_tickers` | *subsumed by digest* | |
| `get_post_comments` | *removed* | RSS doesn't provide comments |
| `get_daily_digest` | `get_daily_digest` | Same output, reads from accumulated cache |
| — | `collect_posts` | **New:** triggers RSS collection round |
| — | `get_collection_stats` | **New:** monitoring |
| — | `reset_collection` | **New:** clear data for new day |

Singleton changes from `_get_scraper() -> RedditScraper` to `_get_collector() -> RSSCollector`. Only needs `user_agent` from settings.

## Step 5: Tests

**`tests/test_mcp_servers/test_reddit.py`**

**Keep:** `TestExtractTickers` (10 tests) and `TestScoreSentiment` (7 tests) — unchanged.

**Remove:** All `TestRedditScraper*` classes (mock asyncpraw tests).

**Add new test classes:**
- `TestRSSCollectorCollect` — deduplication across rounds, accumulation across subreddits, error handling
- `TestRSSCollectorDigest` — output structure matches original format, sentiment direction, subreddit filtering, empty collection
- `TestRSSCollectorStats` — stats after 0 and N collections
- `TestRSSCollectorReset` — clears all state
- `TestHTMLStripping` — tag removal, entity unescaping, whitespace collapsing
- `TestPostIdExtraction` — `t3_` prefix parsing, URL fallback

Mock strategy: patch `httpx.AsyncClient` to return sample Atom XML. Use direct `_posts` dict injection for digest/stats/reset tests (no HTTP needed).

## Step 6: Validate

```bash
uv run ruff check src/ --fix && uv run ruff format src/
uv run pytest tests/ -v
```

## Trade-offs

- **No upvote weighting** — RSS doesn't include scores. Mention count becomes the primary signal. LLM agent's qualitative analysis compensates.
- **No search/comments** — Acceptable; we never used these in the pipeline. The daily digest is the only data the agent pipeline consumes.
- **Rate limits** — Reddit may rate-limit unauthenticated RSS. Mitigated with semaphore (max 5 concurrent) + 200ms delay between requests + proper User-Agent.

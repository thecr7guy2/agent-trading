# Phase 2: MCP Servers Implementation Plan

## Context

Phase 1 (Foundation) is complete: config, Pydantic models, DB schema, base agent, and tests. Phase 2 builds the three MCP servers that expose tools for Reddit scraping, market data, and trading/portfolio management. These servers are standalone processes using the `mcp` Python SDK (`FastMCP`) with stdio transport.

## Implementation Order

Build servers in this order — simplest to most complex:

1. **Market Data MCP Server** — No auth needed (yfinance is free), pure data + math, establishes MCP server pattern
2. **Reddit MCP Server** — Needs Reddit API creds, read-only, moderate complexity
3. **Trading & Portfolio MCP Server** — Most complex: Trading 212 API + database writes + portfolio logic

## Step 1: Add Dependencies

**File:** `pyproject.toml`

Add to `dependencies`:
```
"mcp>=1.0",
"asyncpraw>=7.8",
"yfinance>=0.2",
"httpx>=0.27",
```

Run `uv sync` after.

## Step 2: Market Data MCP Server

### `src/mcp_servers/market_data/finance.py`
yfinance wrapper + technical indicator calculations:
- `EU_SUFFIXES` dict mapping exchange codes to names
- `get_ticker_info(ticker)` — current price via `yf.Ticker().info` wrapped in `asyncio.to_thread()`
- `get_ticker_history(ticker, period)` — OHLCV data, DataFrame → list[dict]
- `get_ticker_fundamentals(ticker)` — P/E, market cap, EPS, dividend yield
- `compute_rsi(closes, period=14)` — pure math, standard RSI formula
- `compute_macd(closes)` — EMA(12) - EMA(26), signal EMA(9)
- `compute_bollinger_bands(closes, period=20)` — SMA +/- 2 std deviations
- `compute_moving_averages(closes)` — SMA/EMA for 10, 20, 50, 200 periods
- `get_technical_indicators_for_ticker(ticker)` — orchestrates history fetch + all compute functions
- `search_eu_stocks_by_query(query)` — try query with each EU suffix
- `is_eu_market_open()` — check CET time + weekday

Key: yfinance is synchronous → all yf calls wrapped with `asyncio.to_thread()`. Technical indicator functions are pure math (no I/O).

### `src/mcp_servers/market_data/server.py`
6 tools — each a thin wrapper delegating to `finance.py`:
- `get_stock_price(ticker)` → `finance.get_ticker_info`
- `get_stock_history(ticker, days=30)` → `finance.get_ticker_history`
- `get_fundamentals(ticker)` → `finance.get_ticker_fundamentals`
- `get_technical_indicators(ticker)` → `finance.get_technical_indicators_for_ticker`
- `search_eu_stocks(query)` → `finance.search_eu_stocks_by_query`
- `get_market_status()` → `finance.is_eu_market_open`

### `tests/test_mcp_servers/test_market_data.py`
- Test all `compute_*` functions with known values (pure math, no mocking)
- Test `get_ticker_info` / `get_ticker_history` with monkeypatched yfinance
- Test `is_eu_market_open` with mocked datetime (weekday vs weekend, market hours)

## Step 3: Reddit MCP Server

### `src/mcp_servers/reddit/scraper.py`
- `TICKER_PATTERN` regex for extracting tickers ($AAPL, ASML.AS)
- `TICKER_BLACKLIST` set (CEO, IPO, ETF, USD, etc.)
- `BULLISH_KEYWORDS` / `BEARISH_KEYWORDS` lists
- `extract_tickers(text)` — regex + blacklist filter + dedup
- `score_sentiment(text, upvotes=1)` — simple keyword-based scoring [-1, 1]
- `RedditScraper` class managing asyncpraw lifecycle:
  - `search_subreddit(subreddit, query, limit)` — search posts
  - `get_trending_tickers(subreddits, hours)` — extract most-mentioned tickers from recent posts
  - `get_post_comments(post_id, limit)` — fetch top-level comments
  - `get_daily_digest(subreddits)` — aggregated stock mentions + sentiment per ticker

Key: asyncpraw in read-only mode (no username/password). Sentiment scoring is deliberately simple — LLM agents do real analysis.

### `src/mcp_servers/reddit/server.py`
4 tools delegating to `RedditScraper`:
- `search_subreddit(subreddit, query, limit=25)`
- `get_trending_tickers(subreddits=None, hours=24)`
- `get_post_comments(post_id, limit=50)`
- `get_daily_digest(subreddits=None)`

Lazy-initialized scraper singleton using `get_settings()` for Reddit credentials.

### `tests/test_mcp_servers/test_reddit.py`
- Test `extract_tickers` regex (dollar prefix, EU suffixes, blacklist, dedup)
- Test `score_sentiment` (bullish/bearish/neutral text, upvote weighting)
- Test `RedditScraper` methods with mocked asyncpraw

## Step 4: Trading & Portfolio MCP Server

### `src/mcp_servers/trading/t212_client.py`
httpx async client for Trading 212 REST API:
- `T212Client(api_key, use_demo=False)` — persistent httpx.AsyncClient
- `place_market_order(ticker, quantity)` — POST /equity/orders/market
- `get_positions()` — GET /equity/portfolio
- `get_account_cash()` — GET /equity/account/cash
- `cancel_order(order_id)` — DELETE /equity/orders/{order_id}

Key: positive quantity = buy, negative = sell. Error handling via httpx status codes.

### `src/mcp_servers/trading/portfolio.py`
Database operations using `asyncpg.Pool`:
- `PortfolioManager(pool)` — injected connection pool
- `record_trade(...)` — INSERT trade + UPDATE position (transactional, weighted avg price recalc)
- `get_portfolio(llm_name)` — SELECT positions
- `get_trade_history(llm_name, limit)` — SELECT trades ORDER BY created_at DESC
- `calculate_pnl(llm_name, start_date, end_date)` — realized P&L from trades
- `get_leaderboard()` — compare LLMs from latest snapshots
- `save_portfolio_snapshot(...)` — UPSERT daily snapshot

Key: `record_trade` uses DB transaction for atomicity. P&L is realized only (unrealized needs live prices from market data server).

### `src/mcp_servers/trading/server.py`
8 tools:
- `place_buy_order(ticker, amount_eur)` — real buy via T212 + record in DB
- `place_sell_order(ticker, quantity)` — real sell via T212 + record in DB
- `get_positions()` — real positions from T212
- `record_virtual_trade(llm_name, ticker, action, quantity, price)` — virtual trade in DB
- `get_portfolio(llm_name)` — positions from DB
- `get_pnl_report(llm_name, start_date, end_date)` — P&L calculation
- `get_leaderboard()` — LLM comparison
- `get_trade_history(llm_name, limit=50)` — trade history

### `tests/test_mcp_servers/test_trading.py`
- Test `T212Client` with mocked httpx responses
- Test `PortfolioManager.record_trade` (new position, existing position avg price, sell reducing position)
- Test P&L calculation logic
- Test leaderboard sorting

### `tests/test_mcp_servers/conftest.py`
Shared fixtures: `mock_settings`, `mock_pool`, `mock_httpx_client`, `mock_reddit`

## Key Design Decisions

- **Error convention:** Tools return `{"error": "description"}` dicts on failure instead of raising exceptions — keeps MCP server alive
- **yfinance async:** Wrapped with `asyncio.to_thread()` since no async yfinance alternative exists
- **No stdout in servers:** MCP stdio transport uses stdout — all logging via `logging` module to stderr
- **Config:** Each server imports `src.config.get_settings()` directly
- **DB access:** Trading server uses `src.db.connection.get_pool()` singleton
- **T212 ticker format:** Accept standard tickers for now; mapping deferred to Phase 4 integration

## Verification

After each server:
1. `uv run ruff check src/ --fix && uv run ruff format src/`
2. `uv run pytest tests/ -v` — all tests pass
3. `uv run python -m src.mcp_servers.<name>.server` — server starts without crashing (ctrl+C to exit)

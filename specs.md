# Trading Bot - Project Specification

## Overview

A multi-LLM agentic trading system that scrapes Reddit for stock sentiment, analyzes European market data, and places real trades (~10 EUR/day) via Trading 212. Two LLMs (Claude and MiniMax 2.5) rotate daily as the "main trader" with real money, while the other makes virtual picks. At the end of each week/month, we compare P&L across both LLMs.

---

## LLM Rotation System

| Day | Main Trader (Real Money) | Virtual Trader |
|-----|--------------------------|----------------|
| Mon | Claude                   | MiniMax 2.5    |
| Tue | MiniMax 2.5              | Claude         |
| Wed | Claude                   | MiniMax 2.5    |
| Thu | MiniMax 2.5              | Claude         |
| Fri | Claude                   | MiniMax 2.5    |

- Each LLM independently picks up to 3 stocks per day (or goes all-in on 1)
- Budget: ~10 EUR/day for the main trader
- Virtual trader gets a simulated 10 EUR/day budget tracked in DB
- Rotation alternates daily between the two LLMs

---

## Architecture

```
                       ┌─────────────────────┐
                       │   Supervisor Agent   │
                       │     (Python)         │
                       │  Schedules & routes  │
                       └──────┬──────────────┘
                              │
               ┌──────────────┴──────────────┐
               │                             │
        ┌──────▼───────┐            ┌────────▼─────────┐
        │ Claude Pipeline│            │ MiniMax Pipeline │
        │              │            │                  │
        │ ┌──────────┐ │            │ ┌──────────────┐ │
        │ │ Sentiment│ │            │ │  Sentiment   │ │
        │ │ (Haiku)  │ │            │ │  (MiniMax)   │ │
        │ └────┬─────┘ │            │ └──────┬───────┘ │
        │ ┌────▼─────┐ │            │ ┌──────▼───────┐ │
        │ │ Market   │ │            │ │   Market     │ │
        │ │ (Sonnet) │ │            │ │  (MiniMax)   │ │
        │ └────┬─────┘ │            │ └──────┬───────┘ │
        │ ┌────▼─────┐ │            │ ┌──────▼───────┐ │
        │ │ Trader   │ │            │ │   Trader     │ │
        │ │ (Opus)   │ │            │ │  (MiniMax)   │ │
        │ └──────────┘ │            │ └──────────────┘ │
        └──────┬───────┘            └────────┬─────────┘
               │                             │
               └──────────────┬──────────────┘
                              │
                       ┌──────▼──────┐
                       │  MCP Servers │
                       │  (Tools)     │
                       └──────┬──────┘
               ┌──────────────┼────────────────┐
               │              │                │
        ┌──────▼──────┐ ┌────▼──────┐ ┌───────▼───────┐
        │ Reddit MCP  │ │ Market    │ │  Trading &    │
        │ Server      │ │ Data MCP  │ │  Portfolio MCP│
        └─────────────┘ └───────────┘ └───────────────┘
```

---

## MCP Servers (Tool Providers)

### 1. Reddit MCP Server (`mcp-reddit`)
Scrapes and analyzes Reddit for stock-related discussions.

**Tools exposed:**
| Tool | Description |
|------|-------------|
| `search_subreddit` | Search a subreddit for posts matching keywords (tickers, stock names) |
| `get_trending_tickers` | Extract most-mentioned tickers from WSB/investing subs in last N hours |
| `get_post_comments` | Fetch comments from a specific post for deeper sentiment |
| `get_daily_digest` | Aggregated summary of all stock mentions + upvote-weighted sentiment |

**Subreddits to scrape:**
- r/wallstreetbets
- r/investing
- r/stocks
- r/EuropeanStocks (or similar EU-focused subs)
- r/Euronext

**Tech:** Python + PRAW (Reddit API) or async PRAW

---

### 2. Market Data MCP Server (`mcp-market-data`)
Provides stock price data, fundamentals, and technical indicators for EU stocks.

**Tools exposed:**
| Tool | Description |
|------|-------------|
| `get_stock_price` | Current/recent price for a ticker (supports EU exchanges) |
| `get_stock_history` | Historical OHLCV data for a ticker over N days |
| `get_fundamentals` | P/E, market cap, EPS, dividend yield, etc. |
| `get_technical_indicators` | RSI, MACD, moving averages, Bollinger bands |
| `search_eu_stocks` | Search for EU-listed stocks by name or partial ticker |
| `get_market_status` | Check if EU markets are open/closed |

**Tech:** Python + `yfinance` library
- EU tickers use suffixes: `.AS` (Amsterdam), `.PA` (Paris), `.DE` (Frankfurt), `.MI` (Milan), `.MC` (Madrid), `.L` (London)

---

### 3. Trading & Portfolio MCP Server (`mcp-trading`)
Handles trade execution and portfolio tracking.

**Tools exposed:**
| Tool | Description |
|------|-------------|
| `place_buy_order` | Place a real buy order via Trading 212 (main trader only) |
| `place_sell_order` | Place a real sell order via Trading 212 |
| `get_positions` | Get current real positions from Trading 212 |
| `record_virtual_trade` | Record a virtual trade for non-main LLMs |
| `get_portfolio` | Get full portfolio (real + virtual) from PostgreSQL |
| `get_pnl_report` | Calculate P&L for a given LLM over a date range |
| `get_leaderboard` | Compare both LLMs' performance side by side |
| `get_trade_history` | Fetch trade history with filters |

**Tech:** Python + Trading 212 API (REST) + PostgreSQL via `asyncpg`

---

## Agents

### 1. Supervisor Agent
- **Role:** Orchestrates the daily trading cycle
- **Runs as:** A Python process with scheduling (APScheduler or similar)
- **Responsibilities:**
  - Determine which LLM is "main" today (rotation logic)
  - Trigger the daily pipeline at configured times
  - Collect data from Reddit + Market Data MCP servers
  - Fan out the same data to both LLM agents
  - Collect their recommendations
  - Present main trader's picks to user for approval (Telegram/CLI)
  - Execute approved trades (real for main, virtual for others)
  - Handle errors and retries

### 2. LLM Agent Pipeline (mirrored for both Claude and MiniMax)

Each LLM provider runs a 3-stage agent pipeline. The stages are the same for both, but use different models.

#### Stage 1: Sentiment Analyst Agent (cheap/fast)
- **Role:** Processes raw Reddit data, scores sentiment per ticker, filters noise
- **Claude version:** Haiku 4.5 (`claude-haiku-4-5-20251001`)
- **MiniMax version:** MiniMax 2.5 (standard)
- **Input:** Raw Reddit digest (posts, comments, upvotes)
- **Output:** `SentimentReport` — ranked list of tickers with sentiment scores, mention counts, key quotes

#### Stage 2: Market Analyst Agent (mid-tier)
- **Role:** Analyzes price data, fundamentals, and technicals for the tickers surfaced by Stage 1
- **Claude version:** Sonnet 4.5 (`claude-sonnet-4-5-20250929`)
- **MiniMax version:** MiniMax 2.5 (standard)
- **Input:** `SentimentReport` + market data (prices, fundamentals, technicals)
- **Output:** `MarketAnalysis` — each ticker scored on fundamentals, technicals, and risk

#### Stage 3: Trader Agent (heavy hitter)
- **Role:** Makes the final buy/sell decisions based on combined analysis
- **Claude version:** Opus 4.6 (`claude-opus-4-6`)
- **MiniMax version:** MiniMax 2.5 (standard)
- **Input:** `SentimentReport` + `MarketAnalysis` + current portfolio + daily budget (10 EUR)
- **Output:** `DailyPicks`

#### Model Summary

| Stage | Role | Claude Model | MiniMax Model |
|-------|------|-------------|---------------|
| 1 | Sentiment Analyst | Haiku 4.5 | MiniMax 2.5 |
| 2 | Market Analyst | Sonnet 4.5 | MiniMax 2.5 |
| 3 | Trader (decision) | Opus 4.6 | MiniMax 2.5 |

#### Pipeline Output (`DailyPicks`)
```json
{
  "llm": "claude",
  "date": "2026-02-14",
  "picks": [
    {
      "ticker": "ASML.AS",
      "exchange": "Euronext Amsterdam",
      "allocation_pct": 60,
      "reasoning": "Strong momentum, positive WSB sentiment, solid fundamentals...",
      "action": "buy"
    },
    {
      "ticker": "SAP.DE",
      "exchange": "Frankfurt",
      "allocation_pct": 40,
      "reasoning": "...",
      "action": "buy"
    }
  ],
  "sell_recommendations": [],
  "confidence": 0.75,
  "market_summary": "Brief analysis of today's market conditions..."
}
```

### 3. Reporter Agent
- **Role:** Generates P&L reports and performance comparisons
- **Triggered:** On-demand, end of week, end of month
- **Output:** Formatted report showing:
  - Per-LLM P&L (realized + unrealized)
  - Total real money P&L
  - Best/worst picks per LLM
  - Win rate per LLM
  - Leaderboard ranking

---

## Daily Pipeline Flow

```
 08:00  ┌─────────────────────────────────┐
        │ 1. Pre-Market Data Collection   │
        │    - Reddit MCP: get_daily_digest│
        │    - Market MCP: get trending    │
        │      stock prices & fundamentals │
        └──────────────┬──────────────────┘
                       │
 08:30  ┌──────────────▼──────────────────┐
        │ 2. Fan out to both LLM Agents   │
        │    - Same data to each           │
        │    - Each picks stocks           │
        │    - Run in parallel             │
        └──────────────┬──────────────────┘
                       │
 09:00  ┌──────────────▼──────────────────┐
        │ 3. User Approval (Main Trader)  │
        │    - Show main LLM's picks      │
        │    - User approves/rejects/edits│
        │    via Telegram or CLI           │
        └──────────────┬──────────────────┘
                       │
 09:15  ┌──────────────▼──────────────────┐
        │ 4. Execute Trades               │
        │    - Main: real order via Trading 212│
        │    - Other: record_virtual_trade │
        └──────────────┬──────────────────┘
                       │
 17:35  ┌──────────────▼──────────────────┐
        │ 5. End of Day                   │
        │    - Record closing prices       │
        │    - Update P&L for both LLMs    │
        │    - Store daily snapshot         │
        └─────────────────────────────────┘
```

EU market hours: 09:00 - 17:30 CET (varies by exchange)

---

## Database Schema (PostgreSQL)

### Tables

```sql
-- LLM rotation schedule and metadata
CREATE TABLE llm_config (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,      -- 'claude', 'minimax'
    api_provider VARCHAR(100) NOT NULL,
    is_active BOOLEAN DEFAULT true
);

-- Daily stock picks from each LLM
CREATE TABLE daily_picks (
    id SERIAL PRIMARY KEY,
    llm_name VARCHAR(50) NOT NULL,
    pick_date DATE NOT NULL,
    is_main_trader BOOLEAN NOT NULL,       -- was this LLM the real-money trader?
    ticker VARCHAR(20) NOT NULL,
    exchange VARCHAR(50),
    allocation_pct DECIMAL(5,2),
    reasoning TEXT,
    confidence DECIMAL(3,2),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(llm_name, pick_date, ticker)
);

-- All trades (real + virtual)
CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    llm_name VARCHAR(50) NOT NULL,
    trade_date DATE NOT NULL,
    ticker VARCHAR(20) NOT NULL,
    action VARCHAR(10) NOT NULL,           -- 'buy' or 'sell'
    quantity DECIMAL(12,4),
    price_per_share DECIMAL(12,4),
    total_cost DECIMAL(12,2),
    is_real BOOLEAN NOT NULL,              -- true = real Trading 212 trade, false = virtual
    broker_order_id VARCHAR(100),          -- Trading 212 order ID if real
    status VARCHAR(20) DEFAULT 'pending',  -- pending, filled, rejected, cancelled
    created_at TIMESTAMP DEFAULT NOW()
);

-- Current positions (real + virtual per LLM)
CREATE TABLE positions (
    id SERIAL PRIMARY KEY,
    llm_name VARCHAR(50) NOT NULL,
    ticker VARCHAR(20) NOT NULL,
    quantity DECIMAL(12,4) NOT NULL,
    avg_buy_price DECIMAL(12,4) NOT NULL,
    is_real BOOLEAN NOT NULL,
    opened_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(llm_name, ticker, is_real)
);

-- Daily portfolio snapshots for P&L tracking
CREATE TABLE portfolio_snapshots (
    id SERIAL PRIMARY KEY,
    llm_name VARCHAR(50) NOT NULL,
    snapshot_date DATE NOT NULL,
    total_invested DECIMAL(12,2),
    total_value DECIMAL(12,2),
    realized_pnl DECIMAL(12,2),
    unrealized_pnl DECIMAL(12,2),
    is_real BOOLEAN NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(llm_name, snapshot_date, is_real)
);

-- Reddit sentiment data (cached)
CREATE TABLE reddit_sentiment (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    scrape_date DATE NOT NULL,
    mention_count INTEGER,
    avg_sentiment DECIMAL(5,3),            -- -1.0 to 1.0
    top_posts JSONB,                        -- [{title, score, url, subreddit}]
    subreddits JSONB,                       -- breakdown by subreddit
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(ticker, scrape_date)
);
```

---

## Project Structure

```
trading-bot/
├── specs.md                        # This file
├── pyproject.toml                  # Project config (uv/poetry)
├── .env                            # API keys (gitignored)
├── .env.example                    # Template for env vars
│
├── src/
│   ├── __init__.py
│   │
│   ├── mcp_servers/                # MCP server implementations
│   │   ├── __init__.py
│   │   ├── reddit/
│   │   │   ├── __init__.py
│   │   │   ├── server.py           # Reddit MCP server
│   │   │   └── scraper.py          # PRAW-based Reddit scraping logic
│   │   ├── market_data/
│   │   │   ├── __init__.py
│   │   │   ├── server.py           # Market Data MCP server
│   │   │   └── finance.py          # yfinance wrapper for EU stocks
│   │   └── trading/
│   │       ├── __init__.py
│   │       ├── server.py           # Trading & Portfolio MCP server
│   │       ├── t212_client.py      # Trading 212 API wrapper
│   │       └── portfolio.py        # Portfolio tracking logic
│   │
│   ├── agents/                     # LLM agent implementations
│   │   ├── __init__.py
│   │   ├── base_agent.py           # Abstract base class for all agent stages
│   │   ├── sentiment_agent.py      # Stage 1: sentiment analysis from Reddit data
│   │   ├── market_agent.py         # Stage 2: market/technical/fundamental analysis
│   │   ├── trader_agent.py         # Stage 3: final buy/sell decision maker
│   │   ├── pipeline.py             # Runs all 3 stages in sequence for a given LLM provider
│   │   ├── providers/
│   │   │   ├── __init__.py
│   │   │   ├── claude.py           # Claude API wrapper (Haiku/Sonnet/Opus)
│   │   │   └── minimax.py          # MiniMax 2.5 API wrapper (OpenAI-compatible)
│   │   └── prompts/
│   │       ├── sentiment.md        # System prompt for sentiment analysis stage
│   │       ├── market_analysis.md  # System prompt for market analysis stage
│   │       └── trader.md           # System prompt for trading decisions
│   │
│   ├── orchestrator/               # Supervisor / pipeline logic
│   │   ├── __init__.py
│   │   ├── supervisor.py           # Main orchestrator
│   │   ├── scheduler.py            # APScheduler-based daily cron
│   │   ├── rotation.py             # LLM rotation logic
│   │   └── approval.py             # User approval flow (CLI + Telegram)
│   │
│   ├── reporting/                  # P&L and performance reports
│   │   ├── __init__.py
│   │   ├── pnl.py                  # P&L calculation engine
│   │   ├── leaderboard.py          # LLM comparison / leaderboard
│   │   └── formatter.py            # Report formatting (terminal tables, markdown)
│   │
│   ├── db/                         # Database layer
│   │   ├── __init__.py
│   │   ├── connection.py           # asyncpg connection pool
│   │   ├── models.py               # Dataclasses / Pydantic models
│   │   └── migrations/             # SQL migration files
│   │       └── 001_initial.sql
│   │
│   └── config.py                   # Settings, env vars, constants
│
├── tests/
│   ├── test_agents/
│   ├── test_mcp_servers/
│   ├── test_orchestrator/
│   └── test_reporting/
│
└── scripts/
    ├── run_daily.py                # Manual trigger for daily pipeline
    ├── report.py                   # Generate P&L report on demand
    └── setup_db.py                 # Initialize database + run migrations
```

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12+ |
| Package Manager | `uv` |
| MCP SDK | `mcp` (official Python SDK) |
| LLM - Claude | `anthropic` SDK |
| LLM - MiniMax | `openai` SDK (OpenAI-compatible) |
| Reddit API | `asyncpraw` |
| Market Data | `yfinance` |
| Broker | Trading 212 REST API + `httpx` |
| Database | PostgreSQL + `asyncpg` |
| Scheduling | `APScheduler` |
| Data Validation | `pydantic` |
| User Approval | CLI (rich/textual) + optional Telegram bot |
| Testing | `pytest` + `pytest-asyncio` |

---

## API Keys Required

```env
# LLM APIs
ANTHROPIC_API_KEY=sk-ant-...         # Claude
MINIMAX_API_KEY=...                   # MiniMax 2.5

# Reddit
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=trading-bot/1.0

# Broker (Trading 212)
T212_API_KEY=...                      # Trading 212 API key (from app settings)

# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/trading_bot

# Optional
TELEGRAM_BOT_TOKEN=...               # For approval notifications
TELEGRAM_CHAT_ID=...
```

---

## Sell Strategy

Since we buy daily with small amounts, we need a sell strategy:

1. **Hold period:** Each position is held for a configurable number of days (default: 5 trading days)
2. **Stop-loss:** Auto-sell if a position drops more than 10% (configurable)
3. **Take-profit:** Auto-sell if a position gains more than 15% (configurable)
4. **LLM can recommend sells:** During daily analysis, LLMs can also recommend selling existing positions
5. **End-of-week cleanup:** Option to liquidate all positions at end of week

---

## Reporting Output Example

```
╔══════════════════════════════════════════════════════════╗
║           WEEKLY TRADING REPORT (Feb 10-14, 2026)       ║
╠══════════════════════════════════════════════════════════╣
║                                                          ║
║  LEADERBOARD                                             ║
║  ┌────┬────────────┬──────────┬──────────┬────────────┐  ║
║  │ #  │ LLM        │ P&L (€)  │ Win Rate │ Avg Return │  ║
║  ├────┼────────────┼──────────┼──────────┼────────────┤  ║
║  │ 1  │ Claude     │ +4.32    │ 66.7%    │ +2.1%      │  ║
║  │ 2  │ MiniMax    │ +1.15    │ 50.0%    │ +0.8%      │  ║
║  └────┴────────────┴──────────┴──────────┴────────────┘  ║
║                                                          ║
║  YOUR REAL PORTFOLIO                                     ║
║  Total Invested:  €50.00                                 ║
║  Current Value:   €51.87                                 ║
║  Real P&L:        +€1.87 (+3.7%)                         ║
║                                                          ║
║  BEST PICK:  ASML.AS +8.2% (Claude, Mon)                ║
║  WORST PICK: SAP.DE  -4.1% (MiniMax, Tue)               ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
```

---

## Implementation Phases

### Phase 1 - Foundation
- [ ] Project setup (uv, pyproject.toml, directory structure)
- [ ] PostgreSQL database setup + migrations
- [ ] Config management (.env, pydantic settings)
- [ ] Base agent class with structured output

### Phase 2 - MCP Servers
- [ ] Reddit MCP server (scraping + sentiment)
- [ ] Market Data MCP server (yfinance + EU stocks)
- [ ] Trading MCP server (Trading 212 + virtual trades + portfolio)

### Phase 3 - LLM Agents
- [ ] Claude agent implementation
- [ ] MiniMax agent implementation
- [ ] Shared prompts and output parsing

### Phase 4 - Orchestration
- [ ] Supervisor / daily pipeline
- [ ] LLM rotation logic
- [ ] User approval flow (CLI first)
- [ ] Scheduler (APScheduler)

### Phase 5 - Reporting
- [ ] P&L calculation engine
- [ ] Leaderboard / LLM comparison
- [ ] Terminal-formatted reports (rich)

### Phase 6 - Polish & Optional
- [ ] Telegram bot for approvals + notifications
- [ ] Sell strategy automation (stop-loss, take-profit)
- [ ] Historical backtesting mode
- [ ] Dashboard (optional, Streamlit or similar)

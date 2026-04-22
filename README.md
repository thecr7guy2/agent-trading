# Insider Signals Trading Bot

![Python](https://img.shields.io/badge/Python-3.13+-blue)
![Claude](https://img.shields.io/badge/AI-Claude_Opus_4.6-orange)
![License](https://img.shields.io/badge/license-MIT-green)
![Account](https://img.shields.io/badge/account-demo_only-lightgrey)

Autonomous demo-account trading bot that looks for public buy disclosures from two places:

- Corporate insider buys surfaced through [OpenInsider](https://openinsider.com), which aggregates SEC Form 4 filings
- US House and Senate trade disclosures surfaced through [Capitol Trades](https://www.capitoltrades.com)

The bot scores those signals, enriches them with market data, sends the final candidate set to Claude Opus, and places buy orders in a Trading 212 demo account on a schedule.

**Live dashboard:** [thecr7guy2.github.io/agent-trading](https://thecr7guy2.github.io/agent-trading/)

## What The Bot Actually Does

On each run, the pipeline:

1. Pulls recent corporate insider buys from OpenInsider.
2. Pulls recent congressional buy disclosures from Capitol Trades.
3. Groups each source by ticker and scores conviction.
4. Merges overlapping tickers across both sources.
5. Enriches each candidate with fundamentals, technicals, news, earnings, and insider history.
6. Sends the enriched digest directly to Claude Opus.
7. Places demo buy orders based on Claude's output.
8. Writes a report and updates the dashboard.

This is not direct SEC scraping. The current implementation uses `OpenInsider` as the SEC-derived corporate insider source and `Capitol Trades` as the congressional disclosure source.

## Signal Sources

### 1. OpenInsider

Source file: [src/mcp_servers/market_data/insider.py](/Users/sai/Documents/Projects/trading-bot/src/mcp_servers/market_data/insider.py)

The bot scrapes recent open-market purchase transactions from OpenInsider and ignores non-purchase activity.

What gets scored:

- Stake increase percent (`delta_own_pct`)
- Insider seniority
- Recency of the trade

Current conviction formula in code:

```text
conviction_score = delta_own_pct * title_multiplier * exp(-0.2 * days_since_trade)
```

Current title weighting:

- C-suite and similar titles such as CEO, CFO, COO, President, Chairman, CTO: `3x`
- Everyone else: `1x`

An OpenInsider candidate qualifies if any of these are true:

- It is a cluster buy: 2 or more distinct insiders buying the same ticker
- It is a solo C-suite buy with at least a 3% ownership increase
- It is a solo buy of at least $200,000, regardless of title

Default settings:

- Lookback: `5` days
- Minimum raw transaction size scraped: `$25,000`
- Top grouped candidates kept: `25`

### 2. Capitol Trades

Source file: [src/mcp_servers/market_data/capitol_trades.py](/Users/sai/Documents/Projects/trading-bot/src/mcp_servers/market_data/capitol_trades.py)

The bot scrapes recent congressional buy disclosures from Capitol Trades, groups them by ticker, and scores them using estimated trade size and recency.

Current conviction formula in code:

```text
conviction_score = amount_midpoint_usd * exp(-0.2 * days_since_trade_or_publication)
```

Important details:

- Trade sizes are parsed from disclosure ranges such as `50K-100K`
- The midpoint of the disclosed range is used
- Pagination stops once rows are older than the configured lookback

Default settings:

- Lookback: `3` days
- Top grouped candidates kept: `10`

## How The Two Sources Interact

Source file: [src/orchestrator/supervisor.py](/Users/sai/Documents/Projects/trading-bot/src/orchestrator/supervisor.py)

The orchestrator fetches both sources in parallel, merges them by ticker, and enriches the merged set.

If the same ticker appears in both sources:

- The entry is merged into one candidate
- Insider and politician names are combined
- Conviction scores are added together
- Total disclosed value is added together
- The merged source becomes `openinsider+capitol_trades`

After enrichment, the bot also applies two important filters:

- Non-equity instruments such as ETFs, mutual funds, indices, futures, and currencies are removed
- Pure Capitol Trades candidates above the configured market-cap ceiling are removed

Default Capitol Trades market-cap ceiling:

- `$50B`

## Claude's Role

Source files:

- [src/agents/pipeline.py](/Users/sai/Documents/Projects/trading-bot/src/agents/pipeline.py)
- [src/agents/trader_agent.py](/Users/sai/Documents/Projects/trading-bot/src/agents/trader_agent.py)
- [src/agents/prompts/trader_aggressive.md](/Users/sai/Documents/Projects/trading-bot/src/agents/prompts/trader_aggressive.md)

The current implementation has a single decision stage. There is no separate research model in the active pipeline.

Claude Opus receives:

- The fully enriched candidate digest
- Current portfolio positions
- The budget for the run

Claude then returns:

- Up to 5 buy picks
- Allocation percentages
- Reasoning for each pick
- Optional sell recommendations
- A run-level confidence score
- A short market summary

There is also a post-processing rule for congressional signals:

- If Capitol Trades candidates exist but Claude picks none of them, the bot injects the top Capitol Trades candidate by replacing the weakest buy pick

## Order Execution

Orders are sent to a Trading 212 demo account.

Current execution behavior:

- Budget per run defaults to `EUR 1000`
- Max picks per run defaults to `5`
- Existing portfolio exposure is checked before buying
- If one order fails, the executor can move on to the next pick

This repo is configured for demo trading only.

## Dashboard

The dashboard lives in [docs/index.html](/Users/sai/Documents/Projects/trading-bot/docs/index.html) and [docs/data.json](/Users/sai/Documents/Projects/trading-bot/docs/data.json).

It shows:

- Total invested capital
- Current portfolio value
- Unrealized P&L in EUR and percent
- Portfolio history
- S&P 100 comparison
- Open positions
- Latest run details and picks

## Default Schedule

Source file: [src/config.py](/Users/sai/Documents/Projects/trading-bot/src/config.py)

Default schedule:

- Trade runs: Tuesday and Friday at `17:10` Europe/Berlin
- End-of-day snapshot/report: Tuesday and Friday at `17:35` Europe/Berlin
- Dashboard snapshot refresh: Monday to Friday at `10:00` and `15:30` Europe/Berlin

## Stack

- Python 3.13+
- `asyncio`
- `uv`
- Anthropic Claude Opus
- Trading 212 API
- OpenInsider
- Capitol Trades
- yfinance
- NewsAPI
- APScheduler
- GitHub Pages for the dashboard

## Project Layout

```text
src/
├── agents/             # Claude decision stage
├── mcp_servers/
│   ├── market_data/    # OpenInsider, Capitol Trades, yfinance, earnings, news
│   └── trading/        # Trading 212 client and portfolio helpers
├── orchestrator/       # Scheduling, candidate merge/enrichment, execution
├── reporting/          # Daily report and dashboard data generation
├── notifications/      # Telegram alerts
├── utils/              # Recently traded blacklist
└── config.py           # Environment-driven settings

scripts/
├── run_scheduler.py
├── run_daily.py
├── dry_run.py
├── check_schedule.py
└── report.py
```

## Setup

### 1. Install

```bash
git clone https://github.com/thecr7guy2/agent-trading.git
cd agent-trading
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
```

Core variables:

| Variable | Required | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `T212_API_KEY` | Yes | Trading 212 demo API key |
| `NEWS_API_KEY` | No | Optional news enrichment |
| `FMP_API_KEY` | No | Optional fundamentals enrichment fallback |
| `TELEGRAM_BOT_TOKEN` | No | Telegram notifications |
| `TELEGRAM_CHAT_ID` | No | Telegram destination |

Useful strategy and schedule settings:

| Variable | Default |
| --- | --- |
| `BUDGET_PER_RUN_EUR` | `1000.0` |
| `MAX_PICKS_PER_RUN` | `5` |
| `MAX_DEMO_PORTFOLIO_INVESTED_EUR` | `46000.0` |
| `INSIDER_LOOKBACK_DAYS` | `5` |
| `MIN_INSIDER_TICKERS` | `10` |
| `INSIDER_TOP_N` | `25` |
| `RESEARCH_TOP_N` | `15` |
| `CAPITOL_TRADES_ENABLED` | `true` |
| `CAPITOL_TRADES_LOOKBACK_DAYS` | `3` |
| `CAPITOL_TRADES_TOP_N` | `10` |
| `CAPITOL_TRADES_RESERVED_SLOTS` | `3` |
| `CAPITOL_TRADES_MAX_MARKET_CAP` | `50000000000` |
| `SCHEDULER_TRADE_DAYS` | `tue,fri` |
| `SCHEDULER_EXECUTE_TIME` | `17:10` |
| `SCHEDULER_EOD_TIME` | `17:35` |
| `SCHEDULER_SNAPSHOT_TIMES` | `10:00,15:30` |
| `ORCHESTRATOR_TIMEZONE` | `Europe/Berlin` |

### 3. Run it

```bash
uv run python scripts/run_scheduler.py
```

Useful commands:

```bash
uv run python scripts/dry_run.py
uv run python scripts/dry_run.py --budget 1500 --lookback 7
uv run python scripts/run_daily.py
uv run python scripts/check_schedule.py
uv run python scripts/report.py
```

## Limitations

- Uses public disclosures, not private information
- Corporate insider data is sourced through OpenInsider, not scraped directly from the SEC
- Congressional disclosures can be delayed by law
- The strategy is buy-only in its current form
- This is a demo-account experiment, not a production live-trading system
- LLM output is non-deterministic and can vary run to run

## License

MIT

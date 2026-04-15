# 🤖 Insider Trading Bot

![Python](https://img.shields.io/badge/Python-3.13+-blue)
![Claude](https://img.shields.io/badge/AI-Claude_Opus_4.6-orange)
![License](https://img.shields.io/badge/license-MIT-green)
![Account](https://img.shields.io/badge/account-demo_only-lightgrey)

> ⚡ Fully autonomous · Demo account only · No human approval required

**[🔴 Live Dashboard →](https://thecr7guy2.github.io/agent-trading/)**

---

## 💡 The Idea

When a CEO buys shares in their own company with personal money, they're betting with real skin in the game. When a US Senator discloses a stock purchase, they're signaling conviction too. This bot finds both kinds of bets, scores them by conviction, and lets Claude Opus decide what to buy — twice a week, fully on its own.

---

## 🔍 How It Works

| Step | What happens |
|------|-------------|
| 1️⃣ **Find signals** | Scrapes [OpenInsider](https://openinsider.com) for cluster buys and [Capitol Trades](https://www.capitoltrades.com) for politician buy disclosures — in parallel |
| 2️⃣ **Score conviction** | Each filing is scored by seniority (corporate) or trade size × recency (political). Top candidates advance |
| 3️⃣ **Merge & filter** | Same ticker in both sources is combined into a single enriched candidate. Mega-caps (>$50B) from Capitol Trades are dropped — those are routine portfolio moves, not signals |
| 4️⃣ **Enrich data** | Pulls fundamentals, technicals, news, earnings, and insider buy history for each candidate — all in parallel |
| 5️⃣ **AI decides** | Claude Opus 4.6 reads all the data and picks what to buy, how much to allocate, and writes its reasoning |
| 6️⃣ **Place orders** | Orders go to a T212 demo account. If one fails, the bot tries the next pick until the budget is spent |
| 7️⃣ **EOD snapshot** | Markdown report generated, portfolio state captured, dashboard updated |

**Trade schedule:** Tuesday & Friday at **17:10 Berlin time** (configurable)
**Dashboard snapshots:** Monday–Friday at **10:00 and 15:30** (lightweight price refresh)

---

## 🧠 AI Pipeline

| Stage | Model | Role |
|-------|-------|------|
| Portfolio Manager | Claude Opus 4.6 | Reads enriched candidate data → outputs ranked buy list with allocation % and written reasoning |

Candidates from both signal sources flow directly into Claude Opus with full enrichment (fundamentals, technicals, news, insider history). No intermediate research stage.

---

## 📡 Signal Sources

### OpenInsider — Corporate Insiders
Scores formula per transaction: `conviction = stake_increase% × seniority_multiplier × e^(−0.2 × days_since_trade)`

| Factor | Detail |
|--------|--------|
| **Stake increase** | How much of their own holdings they bought (`New` position = 100%) |
| **Seniority** | CEO / CFO / COO / President / CTO / Chairman = **3×** · everyone else = **1×** |
| **Recency decay** | Exponential — a buy from 10 days ago scores ~14% of today's |

> A ticker qualifies if it has **2+ insiders buying** (cluster) **or** a solo C-suite exec with a ≥3% stake increase.

### Capitol Trades — Politician Disclosures
US Members of Congress must disclose stock trades within 45 days. This bot scrapes recent buy disclosures and scores by trade size × recency.

| Filter | Detail |
|--------|--------|
| **Lookback** | Default 3 days of disclosures |
| **Mega-cap filter** | Tickers with market cap >$50B are dropped (AAPL/GOOGL/META buys are routine, not informative) |
| **Guaranteed slot** | If Capitol Trades candidates are available, at least 1 CT pick is guaranteed in the final output |

When the same ticker appears in both sources, the entries are merged into a single candidate with both signals combined.

---

## 🛠 Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.13+ with `asyncio` |
| Package manager | `uv` |
| AI | Claude Opus 4.6 (Anthropic SDK) |
| Broker | Trading 212 REST API (demo) |
| Market data | yfinance · NewsAPI · OpenInsider · Capitol Trades · FMP (optional) |
| Scheduler | APScheduler — cron, 24/7 |
| Notifications | Telegram Bot (optional) |
| Dashboard | GitHub Pages — HTML/JS + Chart.js |
| CI/CD | GitHub Actions → Tailscale VPN → SSH → systemd |

---

## 📁 Project Layout

```
src/
├── agents/             # Claude Opus trader + pipeline
├── mcp_servers/
│   ├── market_data/    # yfinance, NewsAPI, OpenInsider, Capitol Trades, FMP
│   └── trading/        # T212 REST API client + portfolio helpers
├── orchestrator/       # Scheduling, enrichment, trade execution
├── reporting/          # Daily markdown reports + dashboard data
├── notifications/      # Telegram alerts
├── utils/              # 3-day buy blacklist
├── config.py           # All settings via .env
└── models.py           # Shared Pydantic models

scripts/
├── run_scheduler.py    # Start the daemon (24/7 autonomous operation)
├── run_daily.py        # Trigger one decision cycle manually
├── dry_run.py          # Run full pipeline without placing any orders
├── check_schedule.py   # Show next scheduled job times
└── report.py           # Print live P&L from T212

reports/YYYY-MM-DD.md   # Auto-generated daily trading reports
docs/                   # GitHub Pages dashboard (data.json + index.html)
```

---

## 📈 Dashboard

**[thecr7guy2.github.io/agent-trading](https://thecr7guy2.github.io/agent-trading/)**

| Tab | Shows |
|-----|-------|
| **Portfolio** | Total invested vs current value · net P&L · value history chart vs S&P 100 benchmark · open positions with per-ticker returns |
| **Analysis Picks** | One card per run — date, confidence score, insider count, spend, source breakdown (OpenInsider / Capitol Trades), and Claude's written reasoning per stock |

Dashboard data is refreshed twice daily (10:00 and 15:30 Berlin time) via lightweight portfolio snapshot jobs, plus a full update after each EOD run.

---

## ⚙️ Setup

### 1 · Clone & install
```bash
git clone https://github.com/thecr7guy2/agent-trading.git
cd agent-trading
uv sync
```

### 2 · Configure `.env`
```bash
cp .env.example .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | ✅ | Claude API key |
| `T212_API_KEY` | ✅ | Trading 212 demo account key |
| `NEWS_API_KEY` | ➖ | 1,000 req/day free tier — falls back to yfinance |
| `FMP_API_KEY` | ➖ | 250 req/day free tier — falls back to yfinance |
| `TELEGRAM_BOT_TOKEN` | ➖ | For trade notifications |
| `BUDGET_PER_RUN_EUR` | ➖ | Default: `1000.0` |
| `MAX_PICKS_PER_RUN` | ➖ | Default: `5` |
| `MAX_DEMO_PORTFOLIO_INVESTED_EUR` | ➖ | Default: `46000.0` — skip new buys once invested capital reaches this cap |
| `INSIDER_TOP_N` | ➖ | Default: `25` — OpenInsider candidates scored |
| `RESEARCH_TOP_N` | ➖ | Default: `15` — candidates passed to Claude |
| `CAPITOL_TRADES_ENABLED` | ➖ | Default: `true` |
| `CAPITOL_TRADES_TOP_N` | ➖ | Default: `10` — top politician picks to include |
| `CAPITOL_TRADES_RESERVED_SLOTS` | ➖ | Default: `3` — guaranteed CT slots in research pool |
| `CAPITOL_TRADES_MAX_MARKET_CAP` | ➖ | Default: `50000000000` ($50B) — drops mega-caps |
| `SCHEDULER_TRADE_DAYS` | ➖ | Default: `tue,fri` |
| `SCHEDULER_EXECUTE_TIME` | ➖ | Default: `17:10` |
| `SCHEDULER_EOD_TIME` | ➖ | Default: `17:35` |
| `SCHEDULER_SNAPSHOT_TIMES` | ➖ | Default: `10:00,15:30` — intraday dashboard refresh |
| `ORCHESTRATOR_TIMEZONE` | ➖ | Default: `Europe/Berlin` |

### 3 · Start the daemon
```bash
uv run python scripts/run_scheduler.py
```

### 4 · Other useful commands
```bash
# Check live P&L from T212
uv run python scripts/report.py

# Run full pipeline without placing orders (test your setup)
uv run python scripts/dry_run.py
uv run python scripts/dry_run.py --budget 1500 --lookback 7

# Trigger one decision cycle immediately (places real demo orders)
uv run python scripts/run_daily.py

# Show next scheduled job times
uv run python scripts/check_schedule.py
```

---

## 🗓 Scheduler Jobs

| Time | Days | Job |
|------|------|-----|
| `17:10` | Tue + Fri | Trade execution — build digest, run Claude, place orders |
| `17:35` | Tue + Fri | EOD snapshot + daily markdown report + dashboard push |
| `10:00` | Mon–Fri | Lightweight portfolio snapshot → dashboard refresh |
| `15:30` | Mon–Fri | Lightweight portfolio snapshot → dashboard refresh |

All times are in **Europe/Berlin** timezone (configurable). Trade days and snapshot times are configurable via `.env`.

---

## 🚀 Deployment

Push to `master` → GitHub Actions handles the rest:

```
Lint (ruff) → Tailscale VPN → git pull + uv sync → systemctl restart trading-bot
```

Runs as a **systemd service**. No containers, no orchestration overhead.

---

## ⚠️ Limitations

| | |
|--|--|
| 🏦 **Demo only** | All orders go to a T212 practice account — no real money |
| 🚫 **No sell logic** | Bot only buys. Exits are manual or via T212 stop-loss |
| ⏱ **Data lag** | SEC filings appear on OpenInsider within ~2 business days · Congressional disclosures can lag up to 45 days by law |
| 🎲 **Non-determinism** | Claude's output varies between runs. Past performance ≠ future results |
| 🏛️ **Political signal noise** | Politician disclosures are legally delayed and may reflect index funds or ETFs — the mega-cap filter reduces but doesn't eliminate this noise |

---

## 📄 License

MIT

# 🤖 Insider Trading Bot

![Python](https://img.shields.io/badge/Python-3.13+-blue)
![Claude](https://img.shields.io/badge/AI-Claude_Opus_4.6-orange)
![License](https://img.shields.io/badge/license-MIT-green)
![Account](https://img.shields.io/badge/account-demo_only-lightgrey)

> ⚡ Fully autonomous · Demo account only · No human approval required

**[🔴 Live Dashboard →](https://thecr7guy2.github.io/agent-trading/)**

---

## 💡 The Idea

When a CEO buys shares in their own company with personal money, they're betting with real skin in the game. This bot finds those bets, scores them by conviction, and lets Claude Opus decide what to buy — twice a week, fully on its own.

---

## 🔍 How It Works

| Step | What happens |
|------|-------------|
| 1️⃣ **Find signals** | Scrapes [OpenInsider](https://openinsider.com) for cluster buys — 2+ insiders buying at the same company, or a solo C-suite exec making a significant purchase |
| 2️⃣ **Score conviction** | Each filing is scored by seniority, stake increase, and recency. Top 25 candidates advance |
| 3️⃣ **Enrich data** | Pulls fundamentals, technicals, news, earnings, and insider buy history for each candidate — all in parallel |
| 4️⃣ **AI decides** | Claude Opus 4.6 reads all the data and picks what to buy, how much to allocate, and writes its reasoning |
| 5️⃣ **Place orders** | Orders go to a T212 demo account. If one fails, the bot tries the next pick until the budget is spent |
| 6️⃣ **EOD snapshot** | Markdown report generated, portfolio state captured, dashboard updated |

**Schedule:** Tuesday & Friday at **17:10 Berlin time** (configurable)

---

## 🧠 AI Pipeline

| Stage | Model | Role |
|-------|-------|------|
| Portfolio Manager | Claude Opus 4.6 | Reads enriched data → outputs ranked buy list with allocation % and written reasoning |

---

## 📊 Conviction Scoring

```
score = stake_increase% × seniority_multiplier × e^(−0.2 × days_since_trade)
```

| Factor | Detail |
|--------|--------|
| **Stake increase** | How much of their own holdings they bought (`New` position = 100%) |
| **Seniority** | CEO / CFO / COO / President / CTO / Chairman = **3×** · everyone else = **1×** |
| **Recency decay** | Exponential — a buy from 10 days ago scores ~14% of today's |

> A ticker qualifies if it has **2+ insiders buying** (cluster) **or** a solo C-suite exec with a ≥3% stake increase. Scores are summed per ticker; top 25 go to Claude.

---

## 🛠 Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.13+ with `asyncio` |
| Package manager | `uv` |
| AI | Claude Opus 4.6 (Anthropic SDK) |
| Broker | Trading 212 REST API (demo) |
| Market data | yfinance · NewsAPI · OpenInsider · FMP (optional) |
| Scheduler | APScheduler — cron, 24/7 |
| Notifications | Telegram Bot (optional) |
| Dashboard | GitHub Pages — HTML/JS + Chart.js |
| CI/CD | GitHub Actions → Tailscale VPN → SSH → systemd |

---

## 📁 Project Layout

```
src/
├── agents/             # Claude Opus trader + pipeline
├── mcp_servers/        # Market data tools + T212 trading API
├── orchestrator/       # Scheduling, enrichment, trade execution
├── reporting/          # Daily markdown reports + dashboard data
├── notifications/      # Telegram alerts
├── utils/              # 3-day buy blacklist
├── config.py           # All settings via .env
└── models.py           # Shared Pydantic models

scripts/
├── run_scheduler.py    # Start the daemon
└── report.py           # Print live P&L from T212

reports/YYYY-MM-DD.md   # Auto-generated daily reports
docs/                   # GitHub Pages dashboard
```

---

## 📈 Dashboard

| Tab | Shows |
|-----|-------|
| **Portfolio** | Total invested vs current value · net P&L · value history chart · open positions with per-ticker returns |
| **Analysis Picks** | One card per run — date, confidence score, insider count, spend, and Claude's written reasoning per stock |

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
| `NEWS_API_KEY` | ➖ | 1,000 req/day free tier |
| `FMP_API_KEY` | ➖ | 250 req/day free tier |
| `TELEGRAM_BOT_TOKEN` | ➖ | For trade notifications |
| `BUDGET_PER_RUN_EUR` | ➖ | Default: `1000.0` |
| `MAX_PICKS_PER_RUN` | ➖ | Default: `5` |
| `SCHEDULER_TRADE_DAYS` | ➖ | Default: `tue,fri` |
| `SCHEDULER_EXECUTE_TIME` | ➖ | Default: `17:10` |
| `ORCHESTRATOR_TIMEZONE` | ➖ | Default: `Europe/Berlin` |

### 3 · Start the daemon
```bash
uv run python scripts/run_scheduler.py
```

### 4 · Check P&L
```bash
uv run python scripts/report.py
```

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
| ⏱ **Data lag** | SEC filings appear on OpenInsider within ~2 business days — price may have already moved |
| 🎲 **Non-determinism** | Claude's output varies between runs. Past performance ≠ future results |

---

## 📄 License

MIT

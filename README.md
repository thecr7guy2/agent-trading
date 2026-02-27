# Insider Trading Bot

An autonomous stock-picking bot that watches what company executives are buying with their own money, runs the data through AI, and places trades on a demo brokerage account — fully on its own, twice a week.

**[Live Dashboard →](https://thecr7guy2.github.io/agent-trading/)**

---

## The Idea

When a CEO or CFO buys shares in their own company using personal funds, it's one of the strongest signals in public markets — they have more information than anyone else, and they're putting real money behind it.

This bot automates the process of finding those filings, scoring them by conviction, and letting an AI portfolio manager decide what to buy. Everything from data collection to order placement happens without any human input.

---

## How It Works

Twice a week (Tuesday and Friday at 17:10 Berlin time), the bot runs this sequence end to end:

**1. Find the signals**
Scrapes [OpenInsider](https://openinsider.com) for recent cluster buy filings — cases where 2 or more insiders at the same company bought shares around the same time, or a single C-suite exec made a significant purchase.

**2. Score by conviction**
Each filing gets a conviction score based on three things: how much of their own stake the insider bought, their seniority (CEO/CFO/COO get 3× weight), and how recently they bought. Fresh buys from the boss matter more than old ones from a VP.

**3. Enrich the top 25 candidates**
For each candidate, the bot pulls in financial fundamentals, price history, technical indicators, upcoming earnings dates, insider buy history, and recent news — all in parallel.

**4. AI makes the call**
Claude Opus 4.6 acts as the portfolio manager. It reads all the enriched data and decides which stocks to buy, how much to allocate to each, and writes out its reasoning. No buy/sell limits are hardcoded into the logic — the AI owns the decision.

**5. Place the orders**
Orders are placed on a Trading 212 **demo** (practice) account. If a ticker isn't available or an order fails, the bot tries the next pick until the budget is spent or candidates run out.

**6. End-of-day snapshot (17:35)**
Generates a markdown report, captures the portfolio state, and publishes everything to the live dashboard.

---

## What "Fully Autonomous" Means

- No one reviews or approves trades before they're placed
- The bot runs as a 24/7 background service on a server
- A GitHub Actions pipeline handles deploys — push to `master`, server restarts automatically
- The only safety nets are the daily budget cap and a 3-day cooldown on recently bought stocks (to avoid buying the same ticker twice in quick succession)
- **All trades are on a demo/practice account — no real money involved**

---

## The AI Pipeline

The bot uses a single AI stage:

| Stage | Model | Role |
|-------|-------|------|
| Portfolio Manager | Claude Opus 4.6 | Reads all enriched data and makes the final buy decisions — which stocks, how much, and why |

Claude gets the full enriched candidate list (fundamentals, technicals, news, insider history) and the current portfolio, then outputs a ranked buy list with allocation percentages and written reasoning for each pick. You can read its reasoning on the dashboard.

---

## Signal Source — OpenInsider

A ticker qualifies if it has **2+ insiders buying around the same time** (cluster) or a **solo C-suite exec with a ≥3% stake increase**. The conviction score formula:

```
score = stake_increase % × seniority_multiplier × recency_decay
```

- **Seniority multiplier** — CEO/CFO/COO/President/CTO/Chairman: 3×, everyone else: 1×
- **Recency decay** — exponential (`e^−0.2 × days`), so a buy from 10 days ago scores ~14% of today's
- Scores are summed across all transactions for the same ticker; the top 25 go to Claude

---

## Tech Stack

| What | How |
|------|-----|
| Language | Python 3.13+ |
| Package manager | `uv` |
| AI model | Claude Opus 4.6 (Anthropic) |
| Broker | Trading 212 REST API (demo account) |
| Market data | yfinance · NewsAPI · OpenInsider (scraped) · FMP (optional) |
| Scheduling | APScheduler — cron-based, runs 24/7 |
| Notifications | Telegram Bot (optional) |
| Dashboard | GitHub Pages — HTML/JS + Chart.js |
| CI/CD | GitHub Actions → Tailscale VPN → SSH → systemd |
| Testing | pytest + pytest-asyncio |

---

## Project Layout

```
src/
├── agents/             # AI pipeline (Claude Opus trader)
├── mcp_servers/        # Data tools — market data, T212 trading API
├── orchestrator/       # Scheduling, digest building, trade execution
├── reporting/          # Daily markdown reports + dashboard data
├── notifications/      # Telegram alerts
├── utils/              # Blacklist (recently_traded.json)
├── config.py           # All settings via .env
└── models.py           # Shared data models

scripts/
├── run_scheduler.py    # Start the daemon
└── report.py           # Print live P&L from T212

reports/
└── YYYY-MM-DD.md       # Auto-generated daily reports

docs/                   # GitHub Pages dashboard
```

---

## Dashboard

The [live dashboard](https://thecr7guy2.github.io/agent-trading/) updates automatically after each end-of-day run.

**Portfolio tab** — total invested vs current value, net P&L, a value history chart, and a table of every open position with per-ticker returns.

**Analysis Picks tab** — one card per run showing the date, Claude's confidence score, how many insider candidates were found, how much was spent, and the AI's written reasoning for each stock it picked.

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/thecr7guy2/agent-trading.git
cd agent-trading
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
```

Fill in `.env` — required fields:

```env
ANTHROPIC_API_KEY=sk-ant-...
T212_API_KEY=...              # Trading 212 demo account key

# Optional — bot degrades gracefully without these
NEWS_API_KEY=                 # 1,000 req/day free tier
FMP_API_KEY=                  # 250 req/day free tier
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_ENABLED=false

# Tuning (defaults shown)
BUDGET_PER_RUN_EUR=1000.0
MAX_PICKS_PER_RUN=5
INSIDER_LOOKBACK_DAYS=3
INSIDER_TOP_N=25
TRADE_EVERY_N_DAYS=2
SCHEDULER_EXECUTE_TIME=17:10
SCHEDULER_EOD_TIME=17:35
SCHEDULER_TRADE_DAYS=tue,fri
ORCHESTRATOR_TIMEZONE=Europe/Berlin
```

### 3. Start the daemon

```bash
uv run python scripts/run_scheduler.py
```

Runs 24/7 and fires automatically on the configured days and times.

### 4. Check portfolio P&L

```bash
uv run python scripts/report.py
```

---

## Deployment

Included GitHub Actions workflow (`.github/workflows/deploy.yml`) — on every push to `master`:

1. Lint with `ruff`
2. Connect to server over Tailscale VPN
3. `git pull` → `uv sync` → `systemctl restart trading-bot`

Runs as a systemd service. No containers.

---

## Development

```bash
# Lint + format
uv run ruff check src/ --fix && uv run ruff format src/

# Run tests
uv run pytest tests/ -v
```

---

## Limitations

- **Demo account only** — all orders go to a T212 practice account. No real money.
- **No sell automation** — the bot only buys. Exits are manual or via stop-loss orders set in T212 directly.
- **OpenInsider data lag** — SEC filings appear on OpenInsider within ~2 business days. The price may have already moved by then.
- **AI non-determinism** — Claude's output varies between runs. Past performance doesn't predict future results.

---

## License

MIT

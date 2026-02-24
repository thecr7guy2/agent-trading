# Insider Trading Bot

A fully autonomous, agentic trading system that watches corporate insider filings, runs them through a two-stage LLM pipeline, and places buy orders on a Trading 212 demo account — twice a week, no human approval required.

**[Live Dashboard →](https://thecr7guy2.github.io/agent-trading/)**

---

## How It Works

Every Tuesday and Friday at 17:10 Berlin time, the scheduler wakes up and runs this pipeline end-to-end:

```
OpenInsider scrape
       │
       ▼
Conviction scoring  ←── C-suite multiplier × stake increase × recency decay
       │
       ▼
Top 25 candidates
       │
       ▼
Parallel enrichment ←── yfinance (price/fundamentals/technicals) + news + insider history
       │
       ▼
Stage 1 — MiniMax M2.5  ←── Analyst: pros / cons / catalyst per ticker (no verdict)
       │
       ▼
Stage 2 — Claude Opus 4.6  ←── Portfolio manager: ranked buy list + allocation %
       │
       ▼
execute_with_fallback()  ←── Places orders on T212 demo; tries next pick if one fails
       │
       ▼
EOD snapshot (17:35)  ←── Daily markdown report + GitHub Pages dashboard update
```

No database. No approval gates. Positions live in the T212 API; the only local state is a 3-day blacklist (`recently_traded.json`) that prevents buying the same stock twice in quick succession.

---

## LLM Pipeline

Two active stages with distinct roles and models:

| Stage | Agent | Model | Role |
|-------|-------|-------|------|
| 1 | `ResearchAgent` | MiniMax M2.5 | Analyst — structured notes only (pros, cons, catalyst). No verdict. |
| 2 | `TraderAgent` | Claude Opus 4.6 | Portfolio manager — independent buy decisions, allocation %, reasoning |

The two models are deliberately isolated. MiniMax produces research notes that Claude *can* reference but is not bound by. Claude Opus gets the full enriched data alongside MiniMax's notes and makes the final call.

A `RiskReviewAgent` (Stage 3) exists in the codebase but is inactive — Claude Opus's system prompt already encodes hard risk rules (stop-loss at −10%, take-profit at +15%, max hold 5 days).

---

## Signal Source — OpenInsider

All candidates come from [OpenInsider](https://openinsider.com) cluster buy filings.

**Conviction score per transaction:**

```
score = delta_own_pct × title_multiplier × e^(−0.2 × days_since_trade)
```

- **`delta_own_pct`** — how much of their own stake the insider bought (`New` position = 100%)
- **`title_multiplier`** — CEO/CFO/COO/President/CTO/Chairman get **3×**; everyone else **1×**
- **Recency decay** — exponential, so a buy 10 days ago is worth ~14% of today's buy

A ticker qualifies if it has 2+ insiders buying (cluster) **or** a solo C-suite exec with a ≥3% stake increase. Scores are summed across all transactions per ticker. The top 25 by score go into the pipeline.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.13+ |
| Package manager | `uv` |
| Async | asyncio native |
| LLMs | Anthropic SDK (Claude) · OpenAI SDK (MiniMax, OpenAI-compatible) |
| Broker | Trading 212 REST API (demo account) |
| Market data | yfinance · NewsAPI · OpenInsider (scraped) · FMP (optional) |
| Scheduling | APScheduler (`AsyncIOScheduler`, cron) |
| Validation | Pydantic v2 |
| Notifications | Telegram Bot API (optional) |
| Dashboard | GitHub Pages — vanilla HTML/JS + Chart.js + Tailwind CSS |
| CI/CD | GitHub Actions → Tailscale VPN → SSH → systemd restart |
| Linting | Ruff |
| Testing | pytest + pytest-asyncio |

---

## Project Structure

```
src/
├── agents/                # LLM pipeline
│   ├── research_agent.py  # Stage 1 — MiniMax analyst
│   ├── trader_agent.py    # Stage 2 — Claude Opus trader
│   ├── pipeline.py        # Orchestrates both stages
│   ├── providers/         # claude.py, minimax.py wrappers
│   └── prompts/           # System prompts as markdown files
├── mcp_servers/
│   ├── market_data/       # yfinance, NewsAPI, OpenInsider, FMP tools
│   └── trading/           # T212 client + portfolio helpers
├── orchestrator/
│   ├── supervisor.py      # Builds digest, runs pipeline, executes trades
│   ├── scheduler.py       # APScheduler cron jobs
│   ├── trade_executor.py  # execute_with_fallback logic
│   └── rotation.py        # Trading day check
├── reporting/
│   ├── daily_report.py    # Markdown report generation
│   └── dashboard.py       # Writes + pushes docs/data.json
├── notifications/
│   └── telegram.py        # Optional Telegram alerts
├── utils/
│   └── recently_traded.py # 3-day blacklist
├── config.py              # Pydantic Settings (reads .env)
└── models.py              # All Pydantic models

docs/                      # GitHub Pages dashboard
├── index.html             # Single-page dashboard (Portfolio + Picks tabs)
└── data.json              # Auto-updated by EOD job

scripts/
├── run_scheduler.py       # Daemon entrypoint
└── report.py              # Live P&L from T212

reports/
└── YYYY-MM-DD.md          # Daily trading reports
```

---

## Dashboard

The bot auto-commits `docs/data.json` after every EOD run and pushes it to `master`. GitHub Pages picks it up immediately.

**Portfolio tab** — Total invested, current value, net P&L, value history chart, open positions table with per-ticker P&L.

**Analysis Picks tab** — One card per run: date, Claude's confidence score, insider candidate count, how much was spent, and per-ticker reasoning from the trader agent.

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

Fill in `.env`:

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...
MINIMAX_API_KEY=sk-cp-...
MINIMAX_BASE_URL=https://api.minimax.io/v1
T212_API_KEY=...                    # Trading 212 demo account key

# Optional (bot degrades gracefully without these)
NEWS_API_KEY=                       # 1,000 req/day free tier
FMP_API_KEY=                        # 250 req/day free tier
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_ENABLED=false

# Tuning (defaults shown)
BUDGET_PER_RUN_EUR=1000.0
MAX_PICKS_PER_RUN=5
INSIDER_LOOKBACK_DAYS=3
INSIDER_TOP_N=25
TRADE_EVERY_N_DAYS=2
RECENTLY_TRADED_DAYS=3
SCHEDULER_EXECUTE_TIME=17:10
SCHEDULER_EOD_TIME=17:35
SCHEDULER_TRADE_DAYS=tue,fri
ORCHESTRATOR_TIMEZONE=Europe/Berlin
```

### 3. Run the daemon

```bash
uv run python scripts/run_scheduler.py
```

The scheduler runs 24/7 and fires automatically on Tuesday and Friday at the configured times.

### 4. Check portfolio P&L

```bash
uv run python scripts/report.py
```

---

## Deployment

The repo ships with a GitHub Actions workflow (`.github/workflows/deploy.yml`) that deploys on every push to `master`:

1. **Lint** — `ruff check src/ scripts/`
2. **Connect** — Tailscale VPN to the server
3. **Deploy** — `git pull` → `uv sync` → `systemctl restart trading-bot`

The bot runs as a systemd service (`trading-bot.service`) on the server. No containers, no orchestration overhead.

---

## Development

```bash
# Lint + format
uv run ruff check src/ --fix
uv run ruff format src/

# Run tests
uv run pytest tests/ -v

# Start an MCP server standalone (for testing tools)
uv run python -m src.mcp_servers.market_data.server

# Manually trigger a decision cycle (bypasses trading day check)
# Set force=True in supervisor.run_decision_cycle() or call directly
```

---

## Safety & Limitations

- **Demo account only** — all orders go to a T212 practice account. No real money.
- **Budget cap** — `BUDGET_PER_RUN_EUR` is the hard ceiling per run.
- **No sell automation** — the bot only buys. Sell decisions are manual (or via T212 stop-loss orders placed separately).
- **OpenInsider data lag** — filings appear on OpenInsider within 2 business days of the SEC filing. There is no guarantee the price hasn't already moved.
- **LLM non-determinism** — Claude Opus and MiniMax outputs vary between runs. Past performance is not indicative of future results.

---

## License

MIT

# Trading Bot — Server Deployment Guide

Complete guide for deploying the autonomous trading bot to a Linux server. No database required — the bot is stateless except for a small JSON blacklist file and daily report files.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Server Setup](#server-setup)
3. [Application Installation](#application-installation)
4. [Environment Configuration](#environment-configuration)
5. [Systemd Service Setup](#systemd-service-setup)
6. [CI/CD with GitHub Actions](#cicd-with-github-actions)
7. [Monitoring & Logs](#monitoring--logs)
8. [Troubleshooting](#troubleshooting)
9. [Maintenance Commands](#maintenance-commands)

---

## Prerequisites

**Recommended server specs:**
- **OS:** Ubuntu 22.04 LTS or later
- **RAM:** 1GB minimum (2GB recommended)
- **Disk:** 10GB minimum (for logs and daily reports)
- **Network:** Outbound HTTPS access required (T212 API, Anthropic, MiniMax, yfinance, OpenInsider, NewsAPI)

**No database needed.** The bot stores nothing except:
- `recently_traded.json` — 3-day buy blacklist (tiny JSON file)
- `reports/YYYY-MM-DD.md` — one markdown report per trading day
- `logs/YYYY-MM-DD.log` — one log file per trading day

---

## Server Setup

### 1. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env   # or restart shell
uv --version
```

---

## Application Installation

### 1. Clone the repository

```bash
sudo mkdir -p /opt/trading-bot
sudo chown $USER:$USER /opt/trading-bot
git clone https://github.com/thecr7guy2/agent-trading.git /opt/trading-bot
cd /opt/trading-bot
```

### 2. Install dependencies

```bash
uv sync
```

### 3. Smoke test

```bash
uv run python scripts/run_scheduler.py
# Should log schedule and sit idle — Ctrl+C to stop
```

---

## Environment Configuration

### 1. Create `.env`

```bash
cp .env.example .env
nano .env
chmod 600 .env
```

### 2. Required variables

```bash
# LLM APIs (required)
ANTHROPIC_API_KEY=sk-ant-...
MINIMAX_API_KEY=your-minimax-key

# Broker — Demo/Practice account only
T212_API_KEY=your-t212-demo-api-key

# Budget
BUDGET_PER_RUN_EUR=1000.0
MAX_PICKS_PER_RUN=5

# Insider pipeline
INSIDER_LOOKBACK_DAYS=5
INSIDER_TOP_N=25
RESEARCH_TOP_N=10        # max candidates passed to MiniMax (reduce if truncation errors occur)
MIN_INSIDER_TICKERS=10

# Schedule (Europe/Berlin)
ORCHESTRATOR_TIMEZONE=Europe/Berlin
SCHEDULER_EXECUTE_TIME=17:10
SCHEDULER_EOD_TIME=17:35
SCHEDULER_TRADE_DAYS=tue,fri

# Data sources (optional — bot degrades gracefully if missing)
NEWS_API_KEY=
FMP_API_KEY=

# Telegram (optional)
TELEGRAM_ENABLED=false
# TELEGRAM_BOT_TOKEN=
# TELEGRAM_CHAT_ID=
```

---

## Systemd Service Setup

### 1. Create the service file

```bash
sudo nano /etc/systemd/system/trading-bot.service
```

```ini
[Unit]
Description=Trading Bot Scheduler Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=sai
WorkingDirectory=/opt/trading-bot
ExecStart=/home/sai/.local/bin/uv run python scripts/run_scheduler.py
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 2. Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable trading-bot
sudo systemctl start trading-bot
sudo systemctl status trading-bot
```

### 3. Watch live logs

```bash
journalctl -u trading-bot -f
```

---

## CI/CD with GitHub Actions

Every push to `master` automatically: runs ruff lint → SSHes into the server → `git pull` + `uv sync` + `systemctl restart`.

The server is on Tailscale (private network), so the GitHub Actions runner joins the tailnet before connecting.

### GitHub Secrets required

| Secret | Value |
|--------|-------|
| `SSH_HOST` | Tailscale IP of the server (`100.x.x.x`) |
| `SSH_USERNAME` | `sai` |
| `SSH_PRIVATE_KEY` | Contents of `~/.ssh/github_deploy` (private key) |
| `SSH_PORT` | `22` |
| `TAILSCALE_AUTHKEY` | Ephemeral reusable auth key from tailscale.com/admin/settings/keys |

### One-time server setup for CI/CD

**Generate a dedicated deploy SSH key:**
```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
```

**Allow passwordless systemctl restart (required for non-interactive SSH):**
```bash
sudo tee /etc/sudoers.d/trading-bot << 'EOF'
Defaults:sai !requiretty, !use_pty
sai ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart trading-bot, /usr/bin/systemctl status trading-bot --no-pager
EOF
sudo chmod 440 /etc/sudoers.d/trading-bot
```

> **Note:** Both `!requiretty` and `!use_pty` are needed. Ubuntu 22.04+ sets `use_pty` by default which blocks sudo in non-interactive SSH sessions even with NOPASSWD.

**Verify it works without a password prompt:**
```bash
ssh localhost "sudo systemctl restart trading-bot && echo OK"
```

### Workflow file

Located at `.github/workflows/deploy.yml`. The pipeline:
1. **Lint** — `ruff check src/ scripts/` (blocks deploy if it fails)
2. **Deploy** — joins Tailscale, SSHes in, pulls, syncs, restarts service

---

## Monitoring & Logs

### Per-day log files

Each run writes to its own dated log file:
```
logs/YYYY-MM-DD.log    ← full logs from that day's decision + EOD run
reports/YYYY-MM-DD.md  ← markdown summary report
```

### Manual forced run (bypasses schedule check)

```bash
cd /opt/trading-bot
uv run python scripts/run_daily.py --force
```

Useful for testing outside of tue/fri schedule. Produces the same log and report files as an automated run.

### Check next scheduled fire times

```bash
uv run python scripts/check_schedule.py
```

### View portfolio P&L

```bash
uv run python scripts/report.py
```

---

## Troubleshooting

### Service won't start

```bash
sudo journalctl -u trading-bot -n 50 --no-pager
```

### MiniMax JSON truncation errors

MiniMax hit `max_tokens` and returned malformed JSON. Reduce `RESEARCH_TOP_N` in `.env` (try `8`).

### T212 API errors

Common errors:
- `not tradable` — ticker unavailable on T212, fallback executor tries next candidate automatically
- `insufficient funds` — check demo account cash balance vs `BUDGET_PER_RUN_EUR`
- `401 Unauthorized` — API key expired or wrong

### Low signal / skipped run

If `insider_count < MIN_INSIDER_TICKERS`, the run is skipped automatically. Check OpenInsider is reachable and `INSIDER_LOOKBACK_DAYS` is sufficient.

### uv not found in CI/CD SSH session

Non-interactive SSH doesn't load the shell profile. The workflow uses the full path `/home/sai/.local/bin/uv` to avoid this.

---

## Maintenance Commands

```bash
# Restart service
sudo systemctl restart trading-bot

# Stop service
sudo systemctl stop trading-bot

# View live logs
journalctl -u trading-bot -f

# View today's log
cat logs/$(date +%Y-%m-%d).log

# View today's report
cat reports/$(date +%Y-%m-%d).md

# View the 3-day blacklist
cat recently_traded.json

# Clear the blacklist (if needed)
echo '{}' > recently_traded.json

# View current portfolio P&L
uv run python scripts/report.py

# Manual forced run (test outside schedule)
uv run python scripts/run_daily.py --force
```

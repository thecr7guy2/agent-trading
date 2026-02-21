# Trading Bot — Server Deployment Guide

Complete guide for deploying the autonomous trading bot to a Linux server. No database required — the bot is stateless except for a small JSON file and daily report files.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Server Setup](#server-setup)
3. [Application Installation](#application-installation)
4. [Environment Configuration](#environment-configuration)
5. [Systemd Service Setup](#systemd-service-setup)
6. [Monitoring & Logs](#monitoring--logs)
7. [Backup Strategy](#backup-strategy)
8. [Troubleshooting](#troubleshooting)
9. [Security Checklist](#security-checklist)

---

## Prerequisites

**Recommended server specs:**
- **OS:** Ubuntu 22.04 LTS or later
- **RAM:** 1GB minimum (2GB recommended)
- **Disk:** 10GB minimum (for logs and daily reports)
- **Network:** Outbound HTTPS access required (T212 API, Anthropic, MiniMax, yfinance, OpenInsider)

**No database needed** — the bot stores nothing except:
- `recently_traded.json` — 3-day buy blacklist (tiny JSON file)
- `reports/YYYY-MM-DD.md` — one markdown file per trading day
- `logs/scheduler.log` — application logs

---

## Server Setup

### 1. Update system packages

```bash
sudo apt update && sudo apt upgrade -y
```

### 2. Install system dependencies

```bash
sudo apt install -y git curl build-essential libssl-dev
```

### 3. Install Python 3.13

```bash
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install -y python3.13 python3.13-venv python3.13-dev
```

### 4. Install uv (Python package manager)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env
```

Verify:
```bash
uv --version
python3.13 --version
```

### 5. Create a dedicated user (recommended)

```bash
sudo useradd -m -s /bin/bash tradingbot
sudo su - tradingbot
```

---

## Application Installation

### 1. Clone the repository

```bash
cd /home/tradingbot
git clone <your-repo-url> trading-bot
cd trading-bot
```

Or sync from your local machine:
```bash
# Run from your LOCAL machine:
rsync -avz \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='logs/*' \
    --exclude='reports/*' \
    /Users/sai/Documents/Projects/trading-bot/ \
    tradingbot@your-server-ip:/home/tradingbot/trading-bot/
```

### 2. Install Python dependencies

```bash
cd /home/tradingbot/trading-bot
uv sync
```

### 3. Create required directories

```bash
mkdir -p logs reports
```

No database setup needed — that's it.

---

## Environment Configuration

### 1. Create `.env` file

```bash
cp .env.example .env
nano .env
```

### 2. Configure environment variables

```bash
# LLM APIs (required)
ANTHROPIC_API_KEY=sk-ant-...
MINIMAX_API_KEY=your-minimax-key
MINIMAX_BASE_URL=https://api.minimax.io/v1

# Broker — Live account (required, real money)
T212_API_KEY=your-t212-api-key

# Broker — Practice / Demo account (optional, enables aggressive strategy)
T212_PRACTICE_API_KEY=your-t212-demo-key
PRACTICE_DAILY_BUDGET_EUR=500.0

# Data sources (optional — bot degrades gracefully if missing)
NEWS_API_KEY=           # NewsAPI free tier (1000 req/day)
FMP_API_KEY=            # Financial Modeling Prep free tier (250 req/day)

# Trading
DAILY_BUDGET_EUR=10.0
MAX_CANDIDATES=15
RECENTLY_TRADED_DAYS=3

# Orchestration
ORCHESTRATOR_TIMEZONE=Europe/Berlin
SCHEDULER_COLLECT_TIMES=08:00,12:00,16:30
SCHEDULER_EXECUTE_TIME=17:10
SCHEDULER_EOD_TIME=17:35

# Sell automation
SELL_STOP_LOSS_PCT=10.0
SELL_TAKE_PROFIT_PCT=15.0
SELL_MAX_HOLD_DAYS=5
SELL_CHECK_SCHEDULE=09:30,12:30,16:45

# Telegram (optional — no-op if disabled)
TELEGRAM_ENABLED=false
# TELEGRAM_BOT_TOKEN=
# TELEGRAM_CHAT_ID=
```

### 3. Secure the `.env` file

```bash
chmod 600 .env
```

---

## Systemd Service Setup

### 1. Create systemd service file

```bash
sudo nano /etc/systemd/system/trading-bot.service
```

```ini
[Unit]
Description=Trading Bot Autonomous Scheduler
After=network.target

[Service]
Type=simple
User=tradingbot
Group=tradingbot
WorkingDirectory=/home/tradingbot/trading-bot
Environment="PATH=/home/tradingbot/.cargo/bin:/usr/local/bin:/usr/bin:/bin"

ExecStart=/home/tradingbot/.cargo/bin/uv run python scripts/run_scheduler.py

Restart=always
RestartSec=10

StandardOutput=journal
StandardError=journal
SyslogIdentifier=trading-bot

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/tradingbot/trading-bot/logs /home/tradingbot/trading-bot/reports /home/tradingbot/trading-bot/recently_traded.json

[Install]
WantedBy=multi-user.target
```

### 2. Enable and start the service

```bash
sudo systemctl daemon-reload
sudo systemctl enable trading-bot.service
sudo systemctl start trading-bot.service
```

### 3. Check service status

```bash
sudo systemctl status trading-bot.service
```

### 4. View live logs

```bash
# Systemd journal
sudo journalctl -u trading-bot.service -f

# Application log file
tail -f /home/tradingbot/trading-bot/logs/scheduler.log
```

---

## Monitoring & Logs

### Log rotation

Create `/etc/logrotate.d/trading-bot`:

```bash
sudo nano /etc/logrotate.d/trading-bot
```

```
/home/tradingbot/trading-bot/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    notifempty
    missingok
    create 0644 tradingbot tradingbot
}
```

### Health check script

```bash
nano ~/check_trading_bot.sh
```

```bash
#!/bin/bash
if ! systemctl is-active --quiet trading-bot.service; then
    echo "Trading bot is DOWN — restarting" | mail -s "Alert: Trading Bot Down" your@email.com
    sudo systemctl restart trading-bot.service
fi
```

```bash
chmod +x ~/check_trading_bot.sh
```

Add to cron (every 15 minutes):
```bash
crontab -e
# Add:
*/15 * * * * /home/tradingbot/check_trading_bot.sh
```

### Access daily reports

Reports are saved to:
```
/home/tradingbot/trading-bot/reports/YYYY-MM-DD.md
```

Sync to local machine:
```bash
# From your LOCAL machine:
rsync -avz tradingbot@your-server-ip:/home/tradingbot/trading-bot/reports/ \
    ~/trading-bot-reports/
```

---

## Backup Strategy

No database to back up. Just two things matter:

### 1. recently_traded.json (blacklist)

Small file, but losing it means the bot might re-buy recently bought stocks. Back up daily:

```bash
# Add to cron:
0 20 * * * cp /home/tradingbot/trading-bot/recently_traded.json \
    /home/tradingbot/backups/recently_traded_$(date +\%Y\%m\%d).json
```

### 2. Reports and .env

```bash
# Weekly backup of reports and config:
0 2 * * 0 tar -czf /home/tradingbot/backups/state_$(date +\%Y\%m\%d).tar.gz \
    /home/tradingbot/trading-bot/.env \
    /home/tradingbot/trading-bot/reports/ \
    /home/tradingbot/trading-bot/recently_traded.json
```

```bash
mkdir -p /home/tradingbot/backups
```

---

## Troubleshooting

### Service won't start

```bash
# Check logs
sudo journalctl -u trading-bot.service -n 50 --no-pager

# Verify config loads correctly
sudo -u tradingbot bash -c "cd /home/tradingbot/trading-bot && uv run python -c 'from src.config import get_settings; s = get_settings(); print(\"Config OK:\", s.orchestrator_timezone)'"
```

### Scheduler not executing jobs

```bash
# Check timezone
timedatectl

# Verify APScheduler jobs are configured
sudo -u tradingbot bash -c "cd /home/tradingbot/trading-bot && uv run python -c \"
from src.orchestrator.scheduler import OrchestratorScheduler
s = OrchestratorScheduler()
s.configure_jobs()
for job in s.scheduler.get_jobs():
    print(job.id, job.next_run_time)
\""
```

### T212 API errors

```bash
grep -i "T212\|t212\|trading212" logs/scheduler.log | tail -20
```

Common issues:
- `not tradable` — ticker not available on T212, fallback executor tries next candidate automatically
- `insufficient funds` — check account cash balance and `DAILY_BUDGET_EUR` setting
- `401 Unauthorized` — API key expired or wrong key (live vs demo key mismatch)

### Missing dependencies

```bash
cd /home/tradingbot/trading-bot
uv sync --reinstall
```

### API rate limits

```bash
grep -i "rate limit\|429\|error" logs/scheduler.log | tail -20
```

The anthropic SDK has `max_retries=5` configured — rate limits are handled automatically with backoff.

---

## Security Checklist

- [ ] `.env` file has 600 permissions (`chmod 600 .env`)
- [ ] SSH key-based authentication enabled (disable password auth)
- [ ] Firewall configured (only allow SSH inbound)
- [ ] API keys use minimum required permissions (read-only where possible)
- [ ] `T212_API_KEY` is live account key; `T212_PRACTICE_API_KEY` is demo key — don't mix them up
- [ ] Server OS auto-updates enabled
- [ ] Non-root user running the service
- [ ] systemd service hardening enabled (`NoNewPrivileges`, `ProtectSystem`, etc.)

### Basic firewall setup (UFW)

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw enable
```

---

## Maintenance Commands

```bash
# Restart service
sudo systemctl restart trading-bot.service

# Stop service
sudo systemctl stop trading-bot.service

# View live logs
sudo journalctl -u trading-bot.service -f

# Update code from git
cd /home/tradingbot/trading-bot
git pull
uv sync
sudo systemctl restart trading-bot.service

# View current portfolio P&L from T212
uv run python scripts/report.py

# Run manual sell checks
uv run python scripts/run_sell_checks.py

# View the 3-day blacklist
cat recently_traded.json

# Clear the blacklist (if needed)
echo '{}' > recently_traded.json

# Check today's report
cat reports/$(date +%Y-%m-%d).md
```

---

## Post-Deployment Verification

### 1. Verify the service starts cleanly

```bash
sudo systemctl status trading-bot.service
sudo journalctl -u trading-bot.service -n 30
```

### 2. Check the scheduler is configured correctly

```bash
grep "Scheduler running" logs/scheduler.log
```

### 3. Wait for first collection job (08:00 Berlin time)

```bash
tail -f logs/scheduler.log
```

Should see: `Collection round finished`

### 4. Check first report after 17:35

```bash
ls -la reports/
cat reports/$(date +%Y-%m-%d).md
```

### 5. Monitor for 2-3 days before trusting fully

Watch for:
- Reddit collection completing without errors
- Sell checks running at 09:30, 12:30, 16:45
- Trade execution at 17:10 producing buys or clear skip reasons
- Daily reports being written to `reports/`
- Trades appearing in your T212 app

---

## Migration Checklist (Local → Server)

Before moving:
- [ ] Push latest code to git
- [ ] Export `.env` settings
- [ ] Note current `recently_traded.json` contents (copy it to server)
- [ ] Verify all API keys are valid and active

After deployment:
- [ ] Service is running: `systemctl status trading-bot`
- [ ] Logs show no errors: `journalctl -u trading-bot -f`
- [ ] Wait for first scheduled job execution
- [ ] Verify trades appear in T212 app
- [ ] Confirm daily reports are written to `reports/`
- [ ] Monitor for 2-3 days before fully trusting

# Trading Bot Server Deployment Guide

Complete guide for deploying the autonomous trading bot to a Linux server.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Server Setup](#server-setup)
3. [Database Setup](#database-setup)
4. [Application Installation](#application-installation)
5. [Environment Configuration](#environment-configuration)
6. [Systemd Service Setup](#systemd-service-setup)
7. [Monitoring & Logs](#monitoring--logs)
8. [Backup Strategy](#backup-strategy)
9. [Troubleshooting](#troubleshooting)
10. [Security Checklist](#security-checklist)

---

## Prerequisites

**Recommended server specs:**
- **OS:** Ubuntu 22.04 LTS or later
- **RAM:** 2GB minimum (4GB recommended)
- **Disk:** 20GB minimum (for logs, DB, and reports)
- **Network:** Static IP or domain name (optional but recommended)

**Required services:**
- PostgreSQL 14+
- Python 3.12+
- systemd (for service management)

---

## Server Setup

### 1. Update system packages

```bash
sudo apt update && sudo apt upgrade -y
```

### 2. Install dependencies

```bash
# System packages
sudo apt install -y \
    git \
    curl \
    build-essential \
    libssl-dev \
    libpq-dev \
    postgresql \
    postgresql-contrib

# Python 3.12 (if not available, use deadsnakes PPA)
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev
```

### 3. Install uv (Python package manager)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env  # Add to PATH
```

Verify installation:
```bash
uv --version
python3.12 --version
```

### 4. Create a dedicated user (recommended)

```bash
sudo useradd -m -s /bin/bash tradingbot
sudo usermod -aG sudo tradingbot  # Optional: if you need sudo access
sudo su - tradingbot
```

---

## Database Setup

### 1. Configure PostgreSQL

```bash
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

### 2. Create database and user

```bash
sudo -u postgres psql

-- In PostgreSQL shell:
CREATE DATABASE trading_bot;
CREATE USER tradingbot WITH ENCRYPTED PASSWORD 'your_secure_password_here';
GRANT ALL PRIVILEGES ON DATABASE trading_bot TO tradingbot;

-- Grant schema permissions (PostgreSQL 15+)
\c trading_bot
GRANT ALL ON SCHEMA public TO tradingbot;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO tradingbot;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO tradingbot;

\q
```

### 3. Allow local connections

Edit `/etc/postgresql/14/main/pg_hba.conf` (adjust version number):

```bash
sudo nano /etc/postgresql/14/main/pg_hba.conf
```

Add this line before other rules:
```
local   trading_bot     tradingbot                              md5
```

Restart PostgreSQL:
```bash
sudo systemctl restart postgresql
```

### 4. Test connection

```bash
psql -U tradingbot -d trading_bot -h localhost
# Enter password when prompted
\q
```

---

## Application Installation

### 1. Clone the repository

```bash
cd /home/tradingbot
git clone <your-repo-url> trading-bot
cd trading-bot
```

If you're migrating from your local machine, use rsync instead:
```bash
# Run from your LOCAL machine:
rsync -avz --exclude='.venv' --exclude='__pycache__' \
    --exclude='.git' --exclude='logs/*' \
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
mkdir -p logs reports/daily reports/backtests
```

### 4. Run database migrations

```bash
uv run python scripts/setup_db.py
```

---

## Environment Configuration

### 1. Create `.env` file

```bash
cp .env.example .env
nano .env
```

### 2. Configure all environment variables

```bash
# LLM APIs
ANTHROPIC_API_KEY=sk-ant-...
MINIMAX_API_KEY=your-minimax-key
MINIMAX_BASE_URL=https://api.minimaxi.chat/v1

# Reddit (optional for RSS-only mode)
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=trading-bot/1.0

# Trading 212
T212_API_KEY=your-t212-api-key

# Database
DATABASE_URL=postgresql://tradingbot:your_secure_password_here@localhost:5432/trading_bot

# Trading settings
DAILY_BUDGET_EUR=10.0
MARKET_DATA_TICKER_LIMIT=12

# Orchestration
ORCHESTRATOR_TIMEZONE=Europe/Berlin
APPROVAL_TIMEOUT_SECONDS=120
APPROVAL_TIMEOUT_ACTION=approve_all  # autonomous mode
SCHEDULER_COLLECT_TIMES=08:00,12:00,16:30
SCHEDULER_EXECUTE_TIME=17:10
SCHEDULER_EOD_TIME=17:35

# Telegram (optional - for Phase 6 notifications)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Claude models
CLAUDE_HAIKU_MODEL=claude-haiku-4-5-20251001
CLAUDE_SONNET_MODEL=claude-sonnet-4-5-20250929
CLAUDE_OPUS_MODEL=claude-opus-4-6

# MiniMax model
MINIMAX_MODEL=MiniMax-Text-01
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

Paste this configuration:

```ini
[Unit]
Description=Trading Bot Autonomous Scheduler
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=tradingbot
Group=tradingbot
WorkingDirectory=/home/tradingbot/trading-bot
Environment="PATH=/home/tradingbot/.cargo/bin:/usr/local/bin:/usr/bin:/bin"

# Run the scheduler daemon
ExecStart=/home/tradingbot/.cargo/bin/uv run python scripts/run_scheduler.py

# Restart policy
Restart=always
RestartSec=10

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=trading-bot

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/tradingbot/trading-bot/logs /home/tradingbot/trading-bot/reports

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

# Application logs
tail -f /home/tradingbot/trading-bot/logs/scheduler.log
```

---

## Monitoring & Logs

### 1. Log rotation

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

### 2. Monitor service health

Create a simple health check script:

```bash
nano ~/check_trading_bot.sh
```

```bash
#!/bin/bash
if ! systemctl is-active --quiet trading-bot.service; then
    echo "Trading bot service is DOWN!" | mail -s "Alert: Trading Bot Down" your@email.com
    sudo systemctl restart trading-bot.service
fi
```

```bash
chmod +x ~/check_trading_bot.sh
```

Add to cron (runs every 15 minutes):
```bash
crontab -e
```

```
*/15 * * * * /home/tradingbot/check_trading_bot.sh
```

### 3. Daily report access

Daily reports are saved to:
```
/home/tradingbot/trading-bot/reports/daily/YYYY-MM-DD.md
```

You can set up a simple web server or sync them to your local machine:

```bash
# From your LOCAL machine:
rsync -avz tradingbot@your-server-ip:/home/tradingbot/trading-bot/reports/daily/ \
    ~/trading-bot-reports/
```

---

## Backup Strategy

### 1. Database backups

Create backup script:

```bash
nano ~/backup_db.sh
```

```bash
#!/bin/bash
BACKUP_DIR="/home/tradingbot/backups"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p $BACKUP_DIR

pg_dump -U tradingbot -d trading_bot -h localhost \
    > $BACKUP_DIR/trading_bot_$DATE.sql

# Keep only last 30 days of backups
find $BACKUP_DIR -name "trading_bot_*.sql" -mtime +30 -delete

echo "Backup completed: trading_bot_$DATE.sql"
```

```bash
chmod +x ~/backup_db.sh
```

Add to cron (daily at 23:00):
```bash
crontab -e
```

```
0 23 * * * /home/tradingbot/backup_db.sh
```

### 2. Application state backups

```bash
# Backup .env and reports weekly
0 2 * * 0 tar -czf /home/tradingbot/backups/app_state_$(date +\%Y\%m\%d).tar.gz \
    /home/tradingbot/trading-bot/.env \
    /home/tradingbot/trading-bot/reports/
```

---

## Troubleshooting

### Service won't start

```bash
# Check logs
sudo journalctl -u trading-bot.service -n 50 --no-pager

# Check if port conflicts exist
sudo netstat -tulpn | grep python

# Verify environment
sudo -u tradingbot bash -c "cd /home/tradingbot/trading-bot && uv run python -c 'from src.config import get_settings; print(get_settings())'"
```

### Database connection errors

```bash
# Test connection manually
psql -U tradingbot -d trading_bot -h localhost

# Check PostgreSQL status
sudo systemctl status postgresql

# Review PostgreSQL logs
sudo tail -f /var/log/postgresql/postgresql-14-main.log
```

### Scheduler not executing jobs

```bash
# Check timezone settings
timedatectl

# Verify APScheduler jobs are configured
uv run python -c "from src.orchestrator.scheduler import OrchestratorScheduler; s = OrchestratorScheduler(); s.configure_jobs(); print(s.scheduler.get_jobs())"
```

### Missing dependencies

```bash
cd /home/tradingbot/trading-bot
uv sync --reinstall
```

### API rate limits or failures

Check logs for API errors:
```bash
grep -i "error\|exception" logs/scheduler.log | tail -20
```

---

## Security Checklist

- [ ] `.env` file has 600 permissions (not world-readable)
- [ ] PostgreSQL user has strong password
- [ ] PostgreSQL only accepts local connections (or firewall-restricted)
- [ ] SSH key-based authentication enabled (disable password auth)
- [ ] Firewall configured (ufw or iptables)
- [ ] API keys rotated periodically
- [ ] Server OS updates enabled
- [ ] Backups tested and verified
- [ ] Non-root user running the service
- [ ] systemd service hardening enabled

### Basic firewall setup (UFW)

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw enable
```

---

## Post-Deployment Verification

### 1. Run a manual test cycle

```bash
cd /home/tradingbot/trading-bot
uv run python scripts/run_daily.py --no-approval
```

### 2. Verify scheduler is running

```bash
sudo systemctl status trading-bot.service
```

### 3. Check next scheduled job

```bash
grep "Next run time" logs/scheduler.log
```

### 4. Monitor first automated run

Wait until the next scheduled collection time (08:00, 12:00, or 16:30) and watch logs:
```bash
tail -f logs/scheduler.log
```

---

## Maintenance Commands

```bash
# Restart service
sudo systemctl restart trading-bot.service

# Stop service
sudo systemctl stop trading-bot.service

# View logs
sudo journalctl -u trading-bot.service -f

# Update code from git
cd /home/tradingbot/trading-bot
git pull
uv sync
sudo systemctl restart trading-bot.service

# Run manual report
uv run python scripts/report.py --period week

# Check database size
psql -U tradingbot -d trading_bot -c "SELECT pg_size_pretty(pg_database_size('trading_bot'));"
```

---

## Migration Checklist

Before shutting down your local instance:

- [ ] Push latest code to git repository
- [ ] Export local `.env` settings
- [ ] Backup local PostgreSQL database: `pg_dump trading_bot > local_backup.sql`
- [ ] Verify all API keys are valid
- [ ] Test one manual run on server before enabling scheduler
- [ ] Confirm daily reports are being generated
- [ ] Set up email/Telegram alerts for failures
- [ ] Document any custom configurations

After server deployment:

- [ ] Verify service is running: `systemctl status trading-bot`
- [ ] Check logs for errors: `journalctl -u trading-bot -f`
- [ ] Wait for first scheduled job execution
- [ ] Verify trades appear in Trading 212
- [ ] Confirm daily reports are written to `reports/daily/`
- [ ] Test backup restoration process
- [ ] Monitor for 3-5 days before trusting fully

---

## Support

If you encounter issues:

1. Check logs: `sudo journalctl -u trading-bot.service -n 100`
2. Verify environment: `uv run python -c "from src.config import get_settings; print(get_settings())"`
3. Test database: `psql -U tradingbot -d trading_bot -c 'SELECT COUNT(*) FROM trades;'`
4. Review this guide's troubleshooting section

---

## Next Steps (Phase 6)

Once the base system is stable, you can add:
- Automated sell triggers (stop-loss, take-profit)
- Telegram notifications (optional)
- Backtesting engine
- Performance dashboards

See `plans/phase-6-polish-optional.md` for details.

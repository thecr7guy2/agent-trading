# Trading Bot — Useful Dev Commands

## Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run a specific test file
uv run pytest tests/test_orchestrator/test_supervisor.py -v

# Run tests matching a keyword
uv run pytest tests/ -k "sell_strategy" -v

# Run with short output
uv run pytest tests/ -q
```

## Linting

```bash
uv run ruff check src/ --fix
uv run ruff format src/
```

## Running the Bot

```bash
# Start the full scheduler daemon (24/7)
uv run python scripts/run_scheduler.py

# Manually trigger sell checks
uv run python scripts/run_sell_checks.py

# Show current portfolio P&L from T212
uv run python scripts/report.py
uv run python scripts/report.py --account live
uv run python scripts/report.py --account demo
```

## MCP Servers (for development/testing)

```bash
# Start market data server
uv run python -m src.mcp_servers.market_data.server

# Start trading server
uv run python -m src.mcp_servers.trading.server

# Start Reddit server
uv run python -m src.mcp_servers.reddit.server
```

## Quick Smoke Tests

```bash
# Test config loads from .env
uv run python -c "from src.config import get_settings; s = get_settings(); print('Config OK:', s.orchestrator_timezone)"

# Test T212 live connection
uv run python -c "
import asyncio
from src.config import get_settings
from src.mcp_servers.trading.t212_client import T212Client
from src.mcp_servers.trading.portfolio import get_live_positions

async def test():
    s = get_settings()
    t212 = T212Client(api_key=s.t212_api_key, use_demo=False)
    positions = await get_live_positions(t212)
    print(f'Live positions: {len(positions)}')
    for p in positions:
        print(f'  {p[\"ticker\"]}: {p[\"quantity\"]} @ {p[\"avg_buy_price\"]}')

asyncio.run(test())
"

# Test T212 demo connection (if configured)
uv run python -c "
import asyncio
from src.config import get_settings
from src.mcp_servers.trading.t212_client import T212Client
from src.mcp_servers.trading.portfolio import get_demo_positions

async def test():
    s = get_settings()
    if not s.t212_practice_api_key:
        print('No demo account configured')
        return
    t212 = T212Client(api_key=s.t212_practice_api_key, use_demo=True)
    positions = await get_demo_positions(t212)
    print(f'Demo positions: {len(positions)}')

asyncio.run(test())
"

# Test MiniMax connection
uv run python -c "
import asyncio
from src.agents.providers.minimax import MiniMaxProvider
from src.config import get_settings

async def test():
    s = get_settings()
    p = MiniMaxProvider(api_key=s.minimax_api_key, base_url=s.minimax_base_url)
    r = await p._client.chat.completions.create(
        model=s.minimax_model,
        messages=[{'role': 'user', 'content': 'Say hello in one word'}],
    )
    print('MiniMax OK:', r.choices[0].message.content)

asyncio.run(test())
"

# Test signal digest (runs screener + reddit + insider + earnings)
uv run python - <<'PY'
import asyncio
from src.orchestrator.supervisor import Supervisor

async def test():
    supervisor = Supervisor()
    digest = await supervisor.build_signal_digest()
    print(f"Candidates: {len(digest.get('candidates', []))}")
    print(f"Reddit posts: {digest.get('total_posts', 0)}")
    print(f"Screener count: {digest.get('screener_count', 0)}")
    for c in digest.get('candidates', [])[:5]:
        print(f"  {c['ticker']}: sources={c['sources']}")

asyncio.run(test())
PY

# Test insider scraper
uv run python -c "
import asyncio
from src.mcp_servers.market_data.insider import get_insider_cluster_buys

async def test():
    clusters = await get_insider_cluster_buys(days=7)
    print(f'Cluster buys found: {len(clusters)}')
    for c in clusters[:3]:
        print(f'  {c[\"ticker\"]} - {c[\"insider_count\"]} insiders, \${c[\"total_value_usd\"]:,.0f}')

asyncio.run(test())
"

# Test sell strategy (evaluate current positions)
uv run python - <<'PY'
import asyncio
from datetime import date
from src.orchestrator.supervisor import Supervisor

async def test():
    supervisor = Supervisor()
    result = await supervisor.run_sell_checks()
    sells = result.get('executed_sells', [])
    print(f'Sell signals: {len(sells)}')
    for s in sells:
        print(f'  {s}')

asyncio.run(test())
PY
```

## Inspect State

```bash
# View 3-day blacklist
cat recently_traded.json

# Clear the blacklist (use carefully)
echo '{}' > recently_traded.json

# View today's report
cat reports/$(date +%Y-%m-%d).md

# List all reports
ls -la reports/*.md

# Check scheduler log
tail -50 logs/scheduler.log

# Watch live log
tail -f logs/scheduler.log
```

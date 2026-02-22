# Trading Bot — Useful Dev Commands

## Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run a specific test file
uv run pytest tests/test_orchestrator/test_supervisor.py -v

# Run tests matching a keyword
uv run pytest tests/ -k "supervisor" -v

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

# Show current portfolio P&L from T212
uv run python scripts/report.py
uv run python scripts/report.py --account demo
```

## MCP Servers (for development/testing)

```bash
# Start market data server
uv run python -m src.mcp_servers.market_data.server

# Start trading server
uv run python -m src.mcp_servers.trading.server
```

## Quick Smoke Tests

```bash
# Test config loads from .env
uv run python -c "from src.config import get_settings; s = get_settings(); print('Config OK:', s.orchestrator_timezone)"

# Test T212 demo connection
uv run python -c "
import asyncio
from src.config import get_settings
from src.mcp_servers.trading.t212_client import T212Client
from src.mcp_servers.trading.portfolio import get_demo_positions

async def test():
    s = get_settings()
    t212 = T212Client(api_key=s.t212_api_key, use_demo=True)
    positions = await get_demo_positions(t212)
    print(f'Demo positions: {len(positions)}')
    for p in positions:
        print(f'  {p[\"ticker\"]}: {p[\"quantity\"]} @ {p[\"avg_buy_price\"]}')

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

# Test insider scraper
uv run python -c "
import asyncio
from src.mcp_servers.market_data.insider import get_insider_candidates

async def test():
    candidates = await get_insider_candidates(days=7, top_n=10)
    print(f'Candidates found: {len(candidates)}')
    for c in candidates[:3]:
        print(f'  {c[\"ticker\"]} - {c[\"insider_count\"]} insiders, \${c[\"total_value_usd\"]:,.0f}, score={c[\"conviction_score\"]}')

asyncio.run(test())
"

# Test insider digest (full enrichment pipeline)
uv run python - <<'PY'
import asyncio
from src.orchestrator.supervisor import Supervisor

async def test():
    supervisor = Supervisor()
    digest = await supervisor.build_insider_digest()
    print(f"Insider candidates: {digest.get('insider_count', 0)}")
    for c in digest.get('candidates', [])[:5]:
        print(f"  {c['ticker']}: score={c['conviction_score']}, news={len(c.get('news', []))} items")

asyncio.run(test())
PY

# Test full decision cycle (dry run — will actually trade if budget > 0!)
# Only use this to test the pipeline, not in production
uv run python - <<'PY'
import asyncio
from src.orchestrator.supervisor import Supervisor

async def test():
    supervisor = Supervisor()
    # force=True bypasses the trading-day and cadence checks
    result = await supervisor.run_decision_cycle(force=True)
    print(f"Status: {result.get('status')}")
    print(f"Picks: {result.get('picks', [])}")

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
tail -50 run_daily.log

# Watch live log
tail -f run_daily.log
```

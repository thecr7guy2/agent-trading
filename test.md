docker run -d \
  --name trading-bot-postgres \
  -e POSTGRES_USER=trading_bot_user \
  -e POSTGRES_PASSWORD=trading_bot_pass \
  -e POSTGRES_DB=trading_bot \
  -p 5432:5432 \
  -v trading-bot-data:/var/lib/postgresql/data \
  postgres:17-alpine


uv run python -m scripts.setup_db


uv run python -c 'import asyncio
from src.orchestrator.supervisor import Supervisor


uv run python -c "import asyncio
from src.agents.providers.minimax import MiniMaxProvider
from src.config import get_settings
async def test():
    settings = get_settings()
    provider = MiniMaxProvider(
        api_key=settings.minimax_api_key,
        base_url=settings.minimax_base_url,
    )

    response = await provider._client.chat.completions.create(
        model=settings.minimax_model,
        messages=[{'role': 'user', 'content': 'Say hello'}],
    )
    print(f\"MiniMax API works! Response: {response.choices[0].message.content}\")

asyncio.run(test())"


uv run python - <<'PY'
import asyncio
from src.orchestrator.supervisor import Supervisor

async def test() -> None:
    supervisor = Supervisor()
    digest = await supervisor.build_signal_digest()

    print(f"Source type: {digest.get('source_type')}")
    print(f"Total candidates: {len(digest.get('candidates', []))}")
    print(f"Reddit posts: {digest.get('total_posts', 0)}")
    print(f"Screener count: {digest.get('screener_count', 0)}")

    for candidate in digest.get("candidates", [])[:5]:
        print(f"  - {candidate['ticker']}: sources={candidate['sources']}")

asyncio.run(test())
PY


uv run python - <<'PY'
import asyncio
from src.orchestrator.supervisor import Supervisor
async def test() -> None:
    supervisor = Supervisor()

    # First: collect from Reddit RSS
    print("Collecting Reddit posts...")
    collection = await supervisor.collect_reddit_round()
    print(f"Collected {collection.get('total_posts', 0)} posts")

    # Then: build signal digest
    print("\nBuilding signal digest...")
    digest = await supervisor.build_signal_digest()

    print(f"\nSource type: {digest.get('source_type')}")
    print(f"Total candidates: {len(digest.get('candidates', []))}")
    print(f"Reddit posts: {digest.get('total_posts', 0)}")
    print(f"Screener count: {digest.get('screener_count', 0)}")


if __name__ == "__main__":
    asyncio.run(test())
PY


PYTHONPATH=. uv run python scripts/run_daily.py --no-approval --collect-rounds 1 > run_daily.log 2>&1

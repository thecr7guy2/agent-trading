"""Database setup: run migrations and seed initial data."""

import asyncio
from pathlib import Path

import asyncpg

from src.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "src" / "db" / "migrations"

SEED_LLMS = [
    ("claude", "anthropic"),
    ("minimax", "minimax"),
]


async def run_migrations(conn: asyncpg.Connection) -> None:
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for migration_file in migration_files:
        print(f"Running migration: {migration_file.name}")
        sql = migration_file.read_text()
        await conn.execute(sql)
    print(f"Applied {len(migration_files)} migration(s).")


async def seed_data(conn: asyncpg.Connection) -> None:
    for name, provider in SEED_LLMS:
        await conn.execute(
            """
            INSERT INTO llm_config (name, api_provider)
            VALUES ($1, $2)
            ON CONFLICT (name) DO NOTHING
            """,
            name,
            provider,
        )
    print(f"Seeded {len(SEED_LLMS)} LLM config(s).")


async def main() -> None:
    settings = get_settings()
    conn = await asyncpg.connect(dsn=settings.database_url)
    try:
        await run_migrations(conn)
        await seed_data(conn)
        print("Database setup complete.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

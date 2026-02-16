import logging
from datetime import date

import asyncpg

logger = logging.getLogger(__name__)


class BacktestDataSource:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def get_available_dates(self, start: date, end: date) -> list[date]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT scrape_date FROM reddit_sentiment
                WHERE scrape_date BETWEEN $1 AND $2
                ORDER BY scrape_date
                """,
                start,
                end,
            )
        return [r["scrape_date"] for r in rows]

    async def reconstruct_sentiment_digest(self, scrape_date: date) -> dict:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT ticker, mention_count, avg_sentiment, top_posts, subreddits
                FROM reddit_sentiment WHERE scrape_date = $1
                ORDER BY mention_count DESC
                """,
                scrape_date,
            )

        if not rows:
            return {"tickers": [], "total_posts": 0, "error": None}

        tickers = []
        total_mentions = 0
        for r in rows:
            mention_count = r["mention_count"] or 0
            total_mentions += mention_count
            tickers.append(
                {
                    "ticker": r["ticker"],
                    "mention_count": mention_count,
                    "sentiment_score": float(r["avg_sentiment"]) if r["avg_sentiment"] else 0.0,
                    "top_quotes": r["top_posts"] or [],
                    "subreddits": r["subreddits"] or {},
                }
            )

        return {
            "tickers": tickers,
            "total_posts": total_mentions,
            "error": None,
        }

    async def save_backtest_run(
        self,
        name: str,
        start_date: date,
        end_date: date,
        status: str = "running",
        notes: str = "",
    ) -> int:
        async with self._pool.acquire() as conn:
            run_id = await conn.fetchval(
                """
                INSERT INTO backtest_runs (name, start_date, end_date, status, notes)
                VALUES ($1, $2, $3, $4, $5) RETURNING id
                """,
                name,
                start_date,
                end_date,
                status,
                notes,
            )
        return run_id

    async def complete_backtest_run(self, run_id: int, status: str = "completed") -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE backtest_runs SET status = $1, completed_at = NOW()
                WHERE id = $2
                """,
                status,
                run_id,
            )

    async def save_daily_result(
        self,
        run_id: int,
        trade_date: date,
        llm_name: str,
        is_real: bool,
        invested: float,
        value: float,
        realized_pnl: float,
        unrealized_pnl: float,
        trades_json: list | None = None,
    ) -> None:
        import json

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO backtest_daily_results
                    (run_id, trade_date, llm_name, is_real, invested, value,
                     realized_pnl, unrealized_pnl, trades_json)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                """,
                run_id,
                trade_date,
                llm_name,
                is_real,
                invested,
                value,
                realized_pnl,
                unrealized_pnl,
                json.dumps(trades_json or []),
            )

    async def get_backtest_results(self, run_id: int) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT trade_date, llm_name, is_real, invested, value,
                       realized_pnl, unrealized_pnl, trades_json
                FROM backtest_daily_results
                WHERE run_id = $1
                ORDER BY trade_date, llm_name
                """,
                run_id,
            )
        return [
            {
                "trade_date": str(r["trade_date"]),
                "llm_name": r["llm_name"],
                "is_real": r["is_real"],
                "invested": float(r["invested"]),
                "value": float(r["value"]),
                "realized_pnl": float(r["realized_pnl"]),
                "unrealized_pnl": float(r["unrealized_pnl"]),
                "trades_json": r["trades_json"] or [],
            }
            for r in rows
        ]

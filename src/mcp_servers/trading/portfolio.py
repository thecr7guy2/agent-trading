from datetime import date
from decimal import Decimal

import asyncpg

from src.db.models import DailyPicks, Position


class PortfolioManager:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def record_trade(
        self,
        llm_name: str,
        ticker: str,
        action: str,
        quantity: Decimal,
        price_per_share: Decimal,
        is_real: bool,
        broker_order_id: str | None = None,
    ) -> dict:
        total_cost = quantity * price_per_share
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Insert trade
                row = await conn.fetchrow(
                    """
                    INSERT INTO trades
                        (llm_name, trade_date, ticker, action, quantity,
                         price_per_share, total_cost, is_real, broker_order_id, status)
                    VALUES ($1, CURRENT_DATE, $2, $3, $4, $5, $6, $7, $8, 'filled')
                    RETURNING id, trade_date, created_at
                    """,
                    llm_name,
                    ticker,
                    action,
                    quantity,
                    price_per_share,
                    total_cost,
                    is_real,
                    broker_order_id,
                )

                # Update position
                if action == "buy":
                    await self._update_position_buy(
                        conn, llm_name, ticker, quantity, price_per_share, is_real
                    )
                elif action == "sell":
                    await self._update_position_sell(conn, llm_name, ticker, quantity, is_real)

        return {
            "id": row["id"],
            "llm_name": llm_name,
            "trade_date": str(row["trade_date"]),
            "ticker": ticker,
            "action": action,
            "quantity": str(quantity),
            "price_per_share": str(price_per_share),
            "total_cost": str(total_cost),
            "is_real": is_real,
            "broker_order_id": broker_order_id,
            "status": "filled",
        }

    async def _update_position_buy(
        self,
        conn: asyncpg.Connection,
        llm_name: str,
        ticker: str,
        quantity: Decimal,
        price: Decimal,
        is_real: bool,
    ):
        existing = await conn.fetchrow(
            """
            SELECT quantity, avg_buy_price FROM positions
            WHERE llm_name = $1 AND ticker = $2 AND is_real = $3
            """,
            llm_name,
            ticker,
            is_real,
        )
        if existing:
            old_qty = existing["quantity"]
            old_avg = existing["avg_buy_price"]
            new_qty = old_qty + quantity
            new_avg = (old_qty * old_avg + quantity * price) / new_qty
            await conn.execute(
                """
                UPDATE positions SET quantity = $1, avg_buy_price = $2
                WHERE llm_name = $3 AND ticker = $4 AND is_real = $5
                """,
                new_qty,
                new_avg,
                llm_name,
                ticker,
                is_real,
            )
        else:
            await conn.execute(
                """
                INSERT INTO positions (llm_name, ticker, quantity, avg_buy_price, is_real)
                VALUES ($1, $2, $3, $4, $5)
                """,
                llm_name,
                ticker,
                quantity,
                price,
                is_real,
            )

    async def _update_position_sell(
        self,
        conn: asyncpg.Connection,
        llm_name: str,
        ticker: str,
        quantity: Decimal,
        is_real: bool,
    ):
        existing = await conn.fetchrow(
            """
            SELECT quantity FROM positions
            WHERE llm_name = $1 AND ticker = $2 AND is_real = $3
            """,
            llm_name,
            ticker,
            is_real,
        )
        if not existing:
            return
        remaining = existing["quantity"] - quantity
        if remaining <= 0:
            await conn.execute(
                """
                DELETE FROM positions
                WHERE llm_name = $1 AND ticker = $2 AND is_real = $3
                """,
                llm_name,
                ticker,
                is_real,
            )
        else:
            await conn.execute(
                """
                UPDATE positions SET quantity = $1
                WHERE llm_name = $2 AND ticker = $3 AND is_real = $4
                """,
                remaining,
                llm_name,
                ticker,
                is_real,
            )

    async def get_portfolio(self, llm_name: str) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, llm_name, ticker, quantity, avg_buy_price, is_real, opened_at
                FROM positions WHERE llm_name = $1
                ORDER BY opened_at DESC
                """,
                llm_name,
            )
        return [
            {
                "id": r["id"],
                "llm_name": r["llm_name"],
                "ticker": r["ticker"],
                "quantity": str(r["quantity"]),
                "avg_buy_price": str(r["avg_buy_price"]),
                "is_real": r["is_real"],
                "opened_at": str(r["opened_at"]),
            }
            for r in rows
        ]

    async def get_trade_history(self, llm_name: str, limit: int = 50) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, llm_name, trade_date, ticker, action, quantity,
                       price_per_share, total_cost, is_real, broker_order_id,
                       status, created_at
                FROM trades
                WHERE llm_name = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                llm_name,
                limit,
            )
        return [
            {
                "id": r["id"],
                "llm_name": r["llm_name"],
                "trade_date": str(r["trade_date"]),
                "ticker": r["ticker"],
                "action": r["action"],
                "quantity": str(r["quantity"]),
                "price_per_share": str(r["price_per_share"]),
                "total_cost": str(r["total_cost"]),
                "is_real": r["is_real"],
                "broker_order_id": r["broker_order_id"],
                "status": r["status"],
            }
            for r in rows
        ]

    async def calculate_pnl(self, llm_name: str, start_date: date, end_date: date) -> dict:
        async with self._pool.acquire() as conn:
            # Total invested (sum of buy costs in period)
            invested = await conn.fetchval(
                """
                SELECT COALESCE(SUM(total_cost), 0)
                FROM trades
                WHERE llm_name = $1 AND action = 'buy'
                  AND trade_date BETWEEN $2 AND $3 AND status = 'filled'
                """,
                llm_name,
                start_date,
                end_date,
            )

            # Total proceeds from sells (realized)
            proceeds = await conn.fetchval(
                """
                SELECT COALESCE(SUM(total_cost), 0)
                FROM trades
                WHERE llm_name = $1 AND action = 'sell'
                  AND trade_date BETWEEN $2 AND $3 AND status = 'filled'
                """,
                llm_name,
                start_date,
                end_date,
            )

            # Win/loss counts from sell trades
            # A sell is a "win" if sell price > avg buy price for that position
            sell_trades = await conn.fetch(
                """
                SELECT t.ticker, t.price_per_share as sell_price, t.quantity
                FROM trades t
                WHERE t.llm_name = $1 AND t.action = 'sell'
                  AND t.trade_date BETWEEN $2 AND $3 AND t.status = 'filled'
                """,
                llm_name,
                start_date,
                end_date,
            )

            # For each sell, look up what the avg buy price was
            win_count = 0
            loss_count = 0
            for trade in sell_trades:
                buy_avg = await conn.fetchval(
                    """
                    SELECT AVG(price_per_share)
                    FROM trades
                    WHERE llm_name = $1 AND ticker = $2 AND action = 'buy'
                      AND status = 'filled' AND trade_date <= $3
                    """,
                    llm_name,
                    trade["ticker"],
                    end_date,
                )
                if buy_avg and trade["sell_price"] > buy_avg:
                    win_count += 1
                else:
                    loss_count += 1

            total_trades = win_count + loss_count
            realized_pnl = proceeds - invested

        return {
            "llm_name": llm_name,
            "period_start": str(start_date),
            "period_end": str(end_date),
            "total_invested": str(invested),
            "total_proceeds": str(proceeds),
            "realized_pnl": str(realized_pnl),
            "total_sell_trades": total_trades,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": round(win_count / total_trades, 2) if total_trades > 0 else 0.0,
        }

    async def get_leaderboard(self) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    llm_name,
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN action = 'buy' THEN total_cost ELSE 0 END) as total_invested,
                    SUM(CASE WHEN action = 'sell' THEN total_cost ELSE 0 END) as total_proceeds
                FROM trades
                WHERE status = 'filled'
                GROUP BY llm_name
                ORDER BY (SUM(CASE WHEN action = 'sell' THEN total_cost ELSE 0 END)
                        - SUM(CASE WHEN action = 'buy' THEN total_cost ELSE 0 END)) DESC
                """
            )
        return [
            {
                "llm_name": r["llm_name"],
                "total_trades": r["total_trades"],
                "total_invested": str(r["total_invested"]),
                "total_proceeds": str(r["total_proceeds"]),
                "realized_pnl": str(r["total_proceeds"] - r["total_invested"]),
            }
            for r in rows
        ]

    async def trade_exists(
        self,
        llm_name: str,
        trade_date: date,
        ticker: str,
        action: str,
        is_real: bool,
    ) -> bool:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT 1
                FROM trades
                WHERE llm_name = $1
                  AND trade_date = $2
                  AND ticker = $3
                  AND action = $4
                  AND is_real = $5
                LIMIT 1
                """,
                llm_name,
                trade_date,
                ticker,
                action,
                is_real,
            )
        return value is not None

    async def save_daily_picks(self, picks: DailyPicks, is_main: bool) -> None:
        async with self._pool.acquire() as conn:
            for pick in picks.picks:
                await conn.execute(
                    """
                    INSERT INTO daily_picks
                        (llm_name, pick_date, is_main_trader, ticker, exchange,
                         allocation_pct, reasoning, confidence)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (llm_name, pick_date, ticker)
                    DO UPDATE SET
                        is_main_trader = EXCLUDED.is_main_trader,
                        exchange = EXCLUDED.exchange,
                        allocation_pct = EXCLUDED.allocation_pct,
                        reasoning = EXCLUDED.reasoning,
                        confidence = EXCLUDED.confidence
                    """,
                    picks.llm.value,
                    picks.pick_date,
                    is_main,
                    pick.ticker,
                    pick.exchange,
                    pick.allocation_pct,
                    pick.reasoning,
                    picks.confidence,
                )

    async def get_positions_typed(self, llm_name: str) -> list[Position]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, llm_name, ticker, quantity, avg_buy_price, is_real, opened_at
                FROM positions WHERE llm_name = $1
                ORDER BY opened_at DESC
                """,
                llm_name,
            )
        return [
            Position(
                id=r["id"],
                llm_name=r["llm_name"],
                ticker=r["ticker"],
                quantity=r["quantity"],
                avg_buy_price=r["avg_buy_price"],
                is_real=r["is_real"],
                opened_at=r["opened_at"],
            )
            for r in rows
        ]

    async def save_portfolio_snapshot(
        self,
        llm_name: str,
        snapshot_date: date,
        total_invested: Decimal,
        total_value: Decimal,
        realized_pnl: Decimal,
        unrealized_pnl: Decimal,
        is_real: bool,
    ) -> dict:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO portfolio_snapshots
                    (llm_name, snapshot_date, total_invested, total_value,
                     realized_pnl, unrealized_pnl, is_real)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (llm_name, snapshot_date, is_real)
                DO UPDATE SET
                    total_invested = EXCLUDED.total_invested,
                    total_value = EXCLUDED.total_value,
                    realized_pnl = EXCLUDED.realized_pnl,
                    unrealized_pnl = EXCLUDED.unrealized_pnl
                """,
                llm_name,
                snapshot_date,
                total_invested,
                total_value,
                realized_pnl,
                unrealized_pnl,
                is_real,
            )
        return {
            "llm_name": llm_name,
            "snapshot_date": str(snapshot_date),
            "total_invested": str(total_invested),
            "total_value": str(total_value),
            "realized_pnl": str(realized_pnl),
            "unrealized_pnl": str(unrealized_pnl),
            "is_real": is_real,
        }

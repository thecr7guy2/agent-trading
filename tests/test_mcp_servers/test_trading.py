from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.mcp_servers.trading.portfolio import PortfolioManager
from src.mcp_servers.trading.t212_client import T212Client, T212Error

# --- T212Client ---


class TestT212Client:
    @pytest.mark.asyncio
    async def test_place_market_order_buy(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "order-123",
            "filledQuantity": 0.5,
            "filledValue": 425.0,
            "ticker": "ASML_NL_EQ",
        }

        client = T212Client(api_key="test-key")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        result = await client.place_market_order("ASML_NL_EQ", 0.5)
        assert result["id"] == "order-123"
        assert result["filledQuantity"] == 0.5
        client._client.request.assert_called_once_with(
            "POST",
            "/equity/orders/market",
            json={"quantity": 0.5, "ticker": "ASML_NL_EQ"},
        )

    @pytest.mark.asyncio
    async def test_place_market_order_sell(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "order-456", "filledQuantity": -0.3}

        client = T212Client(api_key="test-key")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        result = await client.place_market_order("SAP_DE_EQ", -0.3)
        assert result["id"] == "order-456"

    @pytest.mark.asyncio
    async def test_place_value_order(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "order-789",
            "filledQuantity": 0.012,
            "filledValue": 10.0,
        }

        client = T212Client(api_key="test-key")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        result = await client.place_value_order("ASML_NL_EQ", 10.0)
        assert result["filledValue"] == 10.0
        client._client.request.assert_called_once_with(
            "POST",
            "/equity/orders/market",
            json={"value": 10.0, "ticker": "ASML_NL_EQ"},
        )

    @pytest.mark.asyncio
    async def test_get_positions(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"ticker": "ASML_NL_EQ", "quantity": 0.5, "averagePrice": 850.0},
        ]

        client = T212Client(api_key="test-key")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        result = await client.get_positions()
        assert len(result) == 1
        assert result[0]["ticker"] == "ASML_NL_EQ"

    @pytest.mark.asyncio
    async def test_http_error_raises(self):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request: insufficient funds"

        client = T212Client(api_key="test-key")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        with pytest.raises(T212Error) as exc_info:
            await client.place_market_order("BAD", 1.0)
        assert exc_info.value.status_code == 400
        assert "insufficient funds" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_204_returns_empty_dict(self):
        mock_response = MagicMock()
        mock_response.status_code = 204

        client = T212Client(api_key="test-key")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        result = await client.cancel_order("order-123")
        assert result == {}

    def test_demo_base_url(self):
        client = T212Client(api_key="key", use_demo=True)
        assert client._base_url == T212Client.DEMO_BASE_URL

    def test_live_base_url(self):
        client = T212Client(api_key="key", use_demo=False)
        assert client._base_url == T212Client.LIVE_BASE_URL


# --- PortfolioManager ---

# Helper to create a mock asyncpg pool


def _make_mock_pool():
    conn = AsyncMock()

    # conn.transaction() must return an async context manager
    @asynccontextmanager
    async def _transaction():
        yield

    conn.transaction = _transaction

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


class TestPortfolioManagerRecordTrade:
    @pytest.mark.asyncio
    async def test_record_buy_new_position(self):
        pool, conn = _make_mock_pool()

        # fetchrow for INSERT RETURNING
        conn.fetchrow.return_value = {
            "id": 1,
            "trade_date": date(2026, 2, 15),
            "created_at": "2026-02-15T10:00:00",
        }
        # fetchrow for position lookup (no existing position)
        conn.fetchrow.side_effect = [
            {"id": 1, "trade_date": date(2026, 2, 15), "created_at": "2026-02-15T10:00:00"},
            None,  # No existing position
        ]

        pm = PortfolioManager(pool)
        result = await pm.record_trade(
            llm_name="claude",
            ticker="ASML.AS",
            action="buy",
            quantity=Decimal("0.5"),
            price_per_share=Decimal("850"),
            is_real=False,
        )

        assert result["llm_name"] == "claude"
        assert result["ticker"] == "ASML.AS"
        assert result["action"] == "buy"
        assert result["status"] == "filled"
        # Should have called INSERT for position (no existing)
        assert conn.execute.call_count >= 1

    @pytest.mark.asyncio
    async def test_record_buy_existing_position_avg_price(self):
        pool, conn = _make_mock_pool()

        conn.fetchrow.side_effect = [
            {"id": 2, "trade_date": date(2026, 2, 15), "created_at": "2026-02-15T10:00:00"},
            {"quantity": Decimal("1.0"), "avg_buy_price": Decimal("800")},  # Existing position
        ]

        pm = PortfolioManager(pool)
        result = await pm.record_trade(
            llm_name="claude",
            ticker="ASML.AS",
            action="buy",
            quantity=Decimal("1.0"),
            price_per_share=Decimal("900"),
            is_real=False,
        )

        assert result["action"] == "buy"
        # Should have called UPDATE (not INSERT) on positions
        update_call = conn.execute.call_args_list[-1]
        args = update_call[0]
        assert "UPDATE positions" in args[0]
        # New avg = (1.0 * 800 + 1.0 * 900) / 2.0 = 850
        new_qty = args[1]
        new_avg = args[2]
        assert new_qty == Decimal("2.0")
        assert new_avg == Decimal("850")

    @pytest.mark.asyncio
    async def test_record_sell_reduces_position(self):
        pool, conn = _make_mock_pool()

        conn.fetchrow.side_effect = [
            {"id": 3, "trade_date": date(2026, 2, 15), "created_at": "2026-02-15T10:00:00"},
            {"quantity": Decimal("2.0")},  # Existing position
        ]

        pm = PortfolioManager(pool)
        result = await pm.record_trade(
            llm_name="minimax",
            ticker="SAP.DE",
            action="sell",
            quantity=Decimal("0.5"),
            price_per_share=Decimal("200"),
            is_real=False,
        )

        assert result["action"] == "sell"
        # Should UPDATE position with reduced quantity
        update_call = conn.execute.call_args_list[-1]
        args = update_call[0]
        assert "UPDATE positions" in args[0]
        remaining = args[1]
        assert remaining == Decimal("1.5")

    @pytest.mark.asyncio
    async def test_record_sell_removes_empty_position(self):
        pool, conn = _make_mock_pool()

        conn.fetchrow.side_effect = [
            {"id": 4, "trade_date": date(2026, 2, 15), "created_at": "2026-02-15T10:00:00"},
            {"quantity": Decimal("0.5")},  # Existing position — selling all of it
        ]

        pm = PortfolioManager(pool)
        await pm.record_trade(
            llm_name="claude",
            ticker="ASML.AS",
            action="sell",
            quantity=Decimal("0.5"),
            price_per_share=Decimal("900"),
            is_real=False,
        )

        # Should DELETE the position (quantity reaches 0)
        delete_call = conn.execute.call_args_list[-1]
        args = delete_call[0]
        assert "DELETE FROM positions" in args[0]


class TestPortfolioManagerQueries:
    @pytest.mark.asyncio
    async def test_get_portfolio(self):
        pool, conn = _make_mock_pool()
        conn.fetch.return_value = [
            {
                "id": 1,
                "llm_name": "claude",
                "ticker": "ASML.AS",
                "quantity": Decimal("0.5"),
                "avg_buy_price": Decimal("850"),
                "is_real": False,
                "opened_at": "2026-02-15T10:00:00",
            }
        ]

        pm = PortfolioManager(pool)
        result = await pm.get_portfolio("claude")
        assert len(result) == 1
        assert result[0]["ticker"] == "ASML.AS"
        assert result[0]["quantity"] == "0.5"

    @pytest.mark.asyncio
    async def test_get_trade_history(self):
        pool, conn = _make_mock_pool()
        conn.fetch.return_value = [
            {
                "id": 1,
                "llm_name": "claude",
                "trade_date": date(2026, 2, 15),
                "ticker": "ASML.AS",
                "action": "buy",
                "quantity": Decimal("0.5"),
                "price_per_share": Decimal("850"),
                "total_cost": Decimal("425"),
                "is_real": False,
                "broker_order_id": None,
                "status": "filled",
            }
        ]

        pm = PortfolioManager(pool)
        result = await pm.get_trade_history("claude", limit=10)
        assert len(result) == 1
        assert result[0]["action"] == "buy"
        assert result[0]["total_cost"] == "425"

    @pytest.mark.asyncio
    async def test_get_leaderboard(self):
        pool, conn = _make_mock_pool()
        conn.fetch.return_value = [
            {
                "llm_name": "claude",
                "total_trades": 10,
                "total_invested": Decimal("50"),
                "total_proceeds": Decimal("55"),
            },
            {
                "llm_name": "minimax",
                "total_trades": 10,
                "total_invested": Decimal("50"),
                "total_proceeds": Decimal("48"),
            },
        ]

        pm = PortfolioManager(pool)
        result = await pm.get_leaderboard()
        assert len(result) == 2
        assert result[0]["llm_name"] == "claude"
        assert result[0]["realized_pnl"] == "5"
        assert result[1]["realized_pnl"] == "-2"

    @pytest.mark.asyncio
    async def test_calculate_pnl(self):
        pool, conn = _make_mock_pool()
        # fetchval for invested, proceeds
        # fetch for sell_trades
        conn.fetchval.side_effect = [
            Decimal("100"),  # total invested
            Decimal("110"),  # total proceeds
        ]
        conn.fetch.return_value = [
            {"ticker": "ASML.AS", "sell_price": Decimal("900"), "quantity": Decimal("0.5")},
        ]
        # fetchval for avg buy price of that sell
        conn.fetchval.side_effect = [
            Decimal("100"),  # total invested
            Decimal("110"),  # total proceeds
            Decimal("850"),  # avg buy price for ASML.AS (< 900 sell price => win)
        ]

        pm = PortfolioManager(pool)
        result = await pm.calculate_pnl("claude", date(2026, 2, 1), date(2026, 2, 15))
        assert result["llm_name"] == "claude"
        assert result["realized_pnl"] == "10"
        assert result["win_count"] == 1
        assert result["loss_count"] == 0
        assert result["win_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_save_portfolio_snapshot(self):
        pool, conn = _make_mock_pool()

        pm = PortfolioManager(pool)
        result = await pm.save_portfolio_snapshot(
            llm_name="claude",
            snapshot_date=date(2026, 2, 15),
            total_invested=Decimal("50"),
            total_value=Decimal("52"),
            realized_pnl=Decimal("1.5"),
            unrealized_pnl=Decimal("0.5"),
            is_real=False,
        )

        assert result["llm_name"] == "claude"
        assert result["total_value"] == "52"
        conn.execute.assert_called_once()
        call_sql = conn.execute.call_args[0][0]
        assert "ON CONFLICT" in call_sql

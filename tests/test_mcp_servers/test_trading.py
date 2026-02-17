from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.models import DailyPicks, LLMProvider, Position, StockPick
from src.mcp_servers.trading import server as trading_server
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

        client = T212Client(api_key="test-key", api_secret="test-secret")
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

        client = T212Client(api_key="test-key", api_secret="test-secret")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        result = await client.place_market_order("SAP_DE_EQ", -0.3)
        assert result["id"] == "order-456"

    @pytest.mark.asyncio
    async def test_place_market_order_normalizes_precision_to_3_decimals(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "order-precision"}

        client = T212Client(api_key="test-key", api_secret="test-secret")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        await client.place_market_order("MSFT_US_EQ", 0.0249177713)

        client._client.request.assert_called_once_with(
            "POST",
            "/equity/orders/market",
            json={"quantity": 0.024, "ticker": "MSFT_US_EQ"},
        )

    @pytest.mark.asyncio
    async def test_place_market_order_rejects_quantity_rounded_to_zero(self):
        client = T212Client(api_key="test-key", api_secret="test-secret")
        client._client = AsyncMock()

        with pytest.raises(ValueError) as exc_info:
            await client.place_market_order("MSFT_US_EQ", 0.0004)
        assert "rounds to 0" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_positions(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"ticker": "ASML_NL_EQ", "quantity": 0.5, "averagePrice": 850.0},
        ]

        client = T212Client(api_key="test-key", api_secret="test-secret")
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

        client = T212Client(api_key="test-key", api_secret="test-secret")
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

        client = T212Client(api_key="test-key", api_secret="test-secret")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        result = await client.cancel_order("order-123")
        assert result == {}

    def test_demo_base_url(self):
        client = T212Client(api_key="key", api_secret="secret", use_demo=True)
        assert client._base_url == T212Client.DEMO_BASE_URL

    def test_live_base_url(self):
        client = T212Client(api_key="key", api_secret="secret", use_demo=False)
        assert client._base_url == T212Client.LIVE_BASE_URL

    @pytest.mark.asyncio
    async def test_resolve_ticker_from_eu_suffix(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"ticker": "ASML_NL_EQ"},
            {"ticker": "SAP_DE_EQ"},
        ]

        client = T212Client(api_key="test-key", api_secret="test-secret")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        resolved = await client.resolve_ticker("ASML.AS")
        assert resolved == "ASML_NL_EQ"

    @pytest.mark.asyncio
    async def test_resolve_ticker_uses_cache(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"ticker": "ASML_NL_EQ"}]

        client = T212Client(api_key="test-key", api_secret="test-secret")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        first = await client.resolve_ticker("ASML.AS")
        second = await client.resolve_ticker("ASML.AS")

        assert first == "ASML_NL_EQ"
        assert second == "ASML_NL_EQ"
        client._client.request.assert_called_once_with("GET", "/equity/metadata/instruments")

    @pytest.mark.asyncio
    async def test_resolve_ticker_returns_none_when_missing(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"ticker": "OTHER_US_EQ"}]

        client = T212Client(api_key="test-key", api_secret="test-secret")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        resolved = await client.resolve_ticker("ASML.AS")
        assert resolved is None


class TestTradingServerOrders:
    @pytest.mark.asyncio
    async def test_place_buy_order_records_llm_name(self, monkeypatch):
        mock_t212 = AsyncMock()
        mock_t212.resolve_ticker = AsyncMock(return_value="ASML_NL_EQ")
        mock_t212.place_market_order = AsyncMock(
            return_value={"id": "order-1", "filledQuantity": 0.01, "filledValue": 10.0}
        )
        mock_portfolio = AsyncMock()
        mock_portfolio.record_trade = AsyncMock(return_value={"id": 1})

        monkeypatch.setattr(trading_server, "_get_t212", AsyncMock(return_value=mock_t212))
        monkeypatch.setattr(
            trading_server, "_get_portfolio", AsyncMock(return_value=mock_portfolio)
        )

        result = await trading_server.place_buy_order("claude", "ASML.AS", 10.0, 850.0)
        assert result["status"] == "filled"
        assert result["llm_name"] == "claude"
        assert result["broker_ticker"] == "ASML_NL_EQ"

        record_call = mock_portfolio.record_trade.await_args.kwargs
        assert record_call["llm_name"] == "claude"
        assert record_call["ticker"] == "ASML.AS"
        assert record_call["is_real"] is True

    @pytest.mark.asyncio
    async def test_place_buy_order_rejects_unmapped_ticker(self, monkeypatch):
        mock_t212 = AsyncMock()
        mock_t212.resolve_ticker = AsyncMock(return_value=None)
        mock_portfolio = AsyncMock()

        monkeypatch.setattr(trading_server, "_get_t212", AsyncMock(return_value=mock_t212))
        monkeypatch.setattr(
            trading_server, "_get_portfolio", AsyncMock(return_value=mock_portfolio)
        )

        result = await trading_server.place_buy_order("claude", "UNKNOWN", 10.0, 100.0)
        assert "error" in result
        assert result["ticker"] == "UNKNOWN"


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
        result = await pm.calculate_pnl("claude", date(2026, 2, 1), date(2026, 2, 15), is_real=True)
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


class TestPortfolioManagerDailyPicks:
    @pytest.mark.asyncio
    async def test_save_daily_picks(self):
        pool, conn = _make_mock_pool()
        pm = PortfolioManager(pool)
        picks = DailyPicks(
            llm=LLMProvider.CLAUDE,
            pick_date=date(2026, 2, 16),
            picks=[
                StockPick(ticker="ASML.AS", allocation_pct=60.0, action="buy", exchange="AMS"),
                StockPick(ticker="SAP.DE", allocation_pct=40.0, action="buy", exchange="FRA"),
            ],
            confidence=0.85,
            market_summary="test",
        )

        await pm.save_daily_picks(picks, is_main=True)

        assert conn.execute.call_count == 2
        first_call_args = conn.execute.call_args_list[0][0]
        assert "INSERT INTO daily_picks" in first_call_args[0]
        assert first_call_args[1] == "claude"
        assert first_call_args[4] == "ASML.AS"
        assert first_call_args[5] == "AMS"

    @pytest.mark.asyncio
    async def test_trade_exists_true(self):
        pool, conn = _make_mock_pool()
        conn.fetchval.return_value = 1
        pm = PortfolioManager(pool)

        result = await pm.trade_exists("claude", date(2026, 2, 16), "ASML.AS", "buy", True)

        assert result is True
        call_args = conn.fetchval.call_args[0]
        assert "SELECT 1" in call_args[0]
        assert call_args[1] == "claude"
        assert call_args[3] == "ASML.AS"

    @pytest.mark.asyncio
    async def test_trade_exists_false(self):
        pool, conn = _make_mock_pool()
        conn.fetchval.return_value = None
        pm = PortfolioManager(pool)

        result = await pm.trade_exists("claude", date(2026, 2, 16), "ASML.AS", "buy", True)

        assert result is False

    @pytest.mark.asyncio
    async def test_get_positions_typed(self):
        pool, conn = _make_mock_pool()
        conn.fetch.return_value = [
            {
                "id": 1,
                "llm_name": "claude",
                "ticker": "ASML.AS",
                "quantity": Decimal("0.5"),
                "avg_buy_price": Decimal("850"),
                "is_real": False,
                "opened_at": datetime(2026, 2, 15, 10, 0, 0),
            }
        ]

        pm = PortfolioManager(pool)
        result = await pm.get_positions_typed("claude")

        assert len(result) == 1
        assert isinstance(result[0], Position)
        assert result[0].ticker == "ASML.AS"
        assert isinstance(result[0].quantity, Decimal)
        assert result[0].quantity == Decimal("0.5")

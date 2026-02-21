from unittest.mock import AsyncMock, MagicMock

import pytest

from src.mcp_servers.trading import server as trading_server
from src.mcp_servers.trading.t212_client import T212Client, T212Error


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

    @pytest.mark.asyncio
    async def test_resolve_ticker_prefix_fallback(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"ticker": "STM_US_EQ"},
            {"ticker": "AAPL_US_EQ"},
        ]

        client = T212Client(api_key="test-key", api_secret="test-secret")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        resolved = await client.resolve_ticker("STMPA.PA")
        assert resolved == "STM_US_EQ"

    @pytest.mark.asyncio
    async def test_resolve_ticker_cross_exchange(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"ticker": "RED_ES_EQ"},
            {"ticker": "AAPL_US_EQ"},
        ]

        client = T212Client(api_key="test-key", api_secret="test-secret")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        resolved = await client.resolve_ticker("RED.MC")
        assert resolved == "RED_ES_EQ"

    @pytest.mark.asyncio
    async def test_resolve_ticker_cross_exchange_different_country(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"ticker": "CCEP_US_EQ"},
        ]

        client = T212Client(api_key="test-key", api_secret="test-secret")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        resolved = await client.resolve_ticker("CCEP.AS")
        assert resolved == "CCEP_US_EQ"

    @pytest.mark.asyncio
    async def test_resolve_ticker_name_fallback(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"ticker": "AAPL_US_EQ", "name": "Apple Inc"},
            {"ticker": "0YXG_GB_EQ", "name": "Adyen NV"},
        ]

        client = T212Client(api_key="test-key", api_secret="test-secret")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        resolved = await client.resolve_ticker("ADYEN.AS")
        assert resolved == "0YXG_GB_EQ"

    @pytest.mark.asyncio
    async def test_resolve_ticker_name_fallback_skips_short_base(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"ticker": "0ABC_GB_EQ", "name": "XYZ Holdings Plc"},
        ]

        client = T212Client(api_key="test-key", api_secret="test-secret")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_response)

        resolved = await client.resolve_ticker("XYZ.L")
        assert resolved is None


class TestTradingServerOrders:
    @pytest.mark.asyncio
    async def test_place_buy_order_success(self, monkeypatch):
        mock_t212 = AsyncMock()
        mock_t212.resolve_ticker = AsyncMock(return_value="ASML_NL_EQ")
        mock_t212.get_account_cash = AsyncMock(return_value={"free": 1000.0})
        mock_t212.place_market_order = AsyncMock(
            return_value={"id": "order-1", "filledQuantity": 0.01, "filledValue": 10.0}
        )

        monkeypatch.setattr(trading_server, "_get_t212_live", AsyncMock(return_value=mock_t212))

        result = await trading_server.place_buy_order("ASML.AS", 10.0, 850.0, is_real=True)
        assert result["status"] == "filled"
        assert result["ticker"] == "ASML.AS"
        assert result["broker_ticker"] == "ASML_NL_EQ"
        assert result["is_real"] is True

    @pytest.mark.asyncio
    async def test_place_buy_order_rejects_unmapped_ticker(self, monkeypatch):
        mock_t212 = AsyncMock()
        mock_t212.resolve_ticker = AsyncMock(return_value=None)
        mock_t212.get_account_cash = AsyncMock(return_value={"free": 1000.0})

        monkeypatch.setattr(trading_server, "_get_t212_live", AsyncMock(return_value=mock_t212))

        result = await trading_server.place_buy_order("UNKNOWN", 10.0, 100.0, is_real=True)
        assert "error" in result or result.get("status") == "error"
        assert result["ticker"] == "UNKNOWN"

    @pytest.mark.asyncio
    async def test_place_buy_order_rejects_zero_amount(self):
        result = await trading_server.place_buy_order("ASML.AS", 0.0, 850.0)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_place_buy_order_rejects_empty_ticker(self):
        result = await trading_server.place_buy_order("", 10.0, 850.0)
        assert "error" in result

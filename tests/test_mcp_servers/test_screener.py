from unittest.mock import patch

import pytest

from src.mcp_servers.market_data.screener import (
    _is_valid_eu_ticker,
    screen_all_eu,
    screen_eu_exchange,
)

PATCH_TARGET = "src.mcp_servers.market_data.screener.yf.screen"

ASML_QUOTE = {
    "symbol": "ASML.AS",
    "shortName": "ASML Holding",
    "regularMarketPrice": 850.0,
    "regularMarketChangePercent": 3.5,
    "regularMarketVolume": 1_000_000,
    "marketCap": 300_000_000_000,
}


class TestIsValidEuTicker:
    def test_valid_suffixes(self):
        assert _is_valid_eu_ticker("ASML.AS")
        assert _is_valid_eu_ticker("TTE.PA")
        assert _is_valid_eu_ticker("SAP.DE")
        assert _is_valid_eu_ticker("ENI.MI")
        assert _is_valid_eu_ticker("SAN.MC")
        assert _is_valid_eu_ticker("SHEL.L")

    def test_invalid_suffixes(self):
        assert not _is_valid_eu_ticker("AAPL")
        assert not _is_valid_eu_ticker("MSFT.US")
        assert not _is_valid_eu_ticker("7203.T")


class TestScreenEuExchange:
    @pytest.mark.asyncio
    async def test_returns_eu_tickers(self):
        mock_result = {
            "quotes": [
                ASML_QUOTE,
                {
                    "symbol": "AAPL",
                    "shortName": "Apple Inc",
                    "regularMarketPrice": 180.0,
                    "regularMarketChangePercent": 1.2,
                    "regularMarketVolume": 50_000_000,
                    "marketCap": 3_000_000_000_000,
                },
            ]
        }
        with patch(PATCH_TARGET, return_value=mock_result):
            results = await screen_eu_exchange("AMS", "day_gainers")

        assert len(results) == 1
        assert results[0]["ticker"] == "ASML.AS"
        assert results[0]["name"] == "ASML Holding"

    @pytest.mark.asyncio
    async def test_invalid_query_type(self):
        results = await screen_eu_exchange("AMS", "invalid_type")
        assert results == []

    @pytest.mark.asyncio
    async def test_handles_exception(self):
        with patch(PATCH_TARGET, side_effect=Exception("fail")):
            results = await screen_eu_exchange("AMS", "day_gainers")
        assert results == []


class TestScreenAllEu:
    @pytest.mark.asyncio
    async def test_dedup_tickers(self):
        def mock_screen(*args, **kwargs):
            return {"quotes": [ASML_QUOTE]}

        with patch(PATCH_TARGET, side_effect=mock_screen):
            results = await screen_all_eu(exchanges="AMS", per_query_count=5)

        assert len(results) == 1
        assert results[0]["ticker"] == "ASML.AS"
        assert len(results[0]["screener_hits"]) == 3

    @pytest.mark.asyncio
    async def test_sorted_by_screener_hits(self):
        ing_quote = {
            "symbol": "ING.AS",
            "shortName": "ING",
            "regularMarketPrice": 15.0,
            "regularMarketChangePercent": 1.0,
            "regularMarketVolume": 500_000,
            "marketCap": 50_000_000_000,
        }

        def mock_screen(query, sort_field, sort_asc, size):
            if sort_field == "percentchange" and not sort_asc:
                return {"quotes": [ASML_QUOTE, ing_quote]}
            return {"quotes": [ASML_QUOTE]}

        with patch(PATCH_TARGET, side_effect=mock_screen):
            results = await screen_all_eu(exchanges="AMS", per_query_count=5)

        assert results[0]["ticker"] == "ASML.AS"
        assert len(results[0]["screener_hits"]) >= 2

    @pytest.mark.asyncio
    async def test_empty_on_all_failures(self):
        with patch(PATCH_TARGET, side_effect=Exception("fail")):
            results = await screen_all_eu(exchanges="AMS")
        assert results == []

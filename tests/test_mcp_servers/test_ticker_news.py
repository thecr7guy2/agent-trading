from unittest.mock import MagicMock, patch

import pytest

from src.mcp_servers.market_data.finance import get_ticker_news


class TestGetTickerNews:
    @pytest.mark.asyncio
    async def test_parses_news_content(self):
        mock_news = [
            {
                "content": {
                    "title": "ASML beats estimates",
                    "summary": "Q4 revenue surpasses analyst expectations",
                    "provider": {"displayName": "Reuters"},
                    "pubDate": "2026-02-16T10:00:00Z",
                }
            },
            {
                "content": {
                    "title": "EU chip demand rising",
                    "summary": "Semiconductor sector sees increased orders",
                    "provider": {"displayName": "Bloomberg"},
                    "pubDate": "2026-02-15T14:00:00Z",
                }
            },
        ]
        mock_ticker = MagicMock()
        mock_ticker.news = mock_news

        with patch("src.mcp_servers.market_data.finance.yf.Ticker", return_value=mock_ticker):
            items = await get_ticker_news("ASML.AS", max_items=5)

        assert len(items) == 2
        assert items[0]["title"] == "ASML beats estimates"
        assert items[0]["provider"] == "Reuters"
        assert items[1]["title"] == "EU chip demand rising"

    @pytest.mark.asyncio
    async def test_respects_max_items(self):
        mock_news = [
            {"content": {"title": f"News {i}", "summary": "", "provider": {}, "pubDate": ""}}
            for i in range(10)
        ]
        mock_ticker = MagicMock()
        mock_ticker.news = mock_news

        with patch("src.mcp_servers.market_data.finance.yf.Ticker", return_value=mock_ticker):
            items = await get_ticker_news("ASML.AS", max_items=3)

        assert len(items) == 3

    @pytest.mark.asyncio
    async def test_handles_empty_news(self):
        mock_ticker = MagicMock()
        mock_ticker.news = []

        with patch("src.mcp_servers.market_data.finance.yf.Ticker", return_value=mock_ticker):
            items = await get_ticker_news("ASML.AS")

        assert items == []

    @pytest.mark.asyncio
    async def test_handles_none_news(self):
        mock_ticker = MagicMock()
        mock_ticker.news = None

        with patch("src.mcp_servers.market_data.finance.yf.Ticker", return_value=mock_ticker):
            items = await get_ticker_news("ASML.AS")

        assert items == []

    @pytest.mark.asyncio
    async def test_handles_exception(self):
        with patch(
            "src.mcp_servers.market_data.finance.yf.Ticker",
            side_effect=Exception("API error"),
        ):
            items = await get_ticker_news("ASML.AS")

        assert items == []

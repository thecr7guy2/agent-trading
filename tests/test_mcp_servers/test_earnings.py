from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.mcp_servers.market_data.finance import (
    get_earnings_calendar_upcoming,
    get_ticker_earnings,
)


class TestGetEarningsCalendarUpcoming:
    @pytest.mark.asyncio
    async def test_parses_calendar(self):
        df = pd.DataFrame(
            [
                {
                    "ticker": "ASML.AS",
                    "companyshortname": "ASML Holding",
                    "startdatetype": "Earnings",
                    "startdatetime": "2026-02-20",
                    "epsestimate": 5.50,
                },
                {
                    "ticker": "SAP.DE",
                    "companyshortname": "SAP SE",
                    "startdatetype": "Earnings",
                    "startdatetime": "2026-02-21",
                    "epsestimate": 2.10,
                },
            ]
        )
        mock_cal = MagicMock()
        mock_cal.get_earnings_calendar.return_value = df

        with patch("src.mcp_servers.market_data.finance.yf.Calendars", return_value=mock_cal):
            events = await get_earnings_calendar_upcoming()

        assert len(events) == 2
        assert events[0]["ticker"] == "ASML.AS"
        assert events[0]["company"] == "ASML Holding"
        assert events[1]["ticker"] == "SAP.DE"

    @pytest.mark.asyncio
    async def test_handles_empty_df(self):
        mock_cal = MagicMock()
        mock_cal.get_earnings_calendar.return_value = pd.DataFrame()

        with patch("src.mcp_servers.market_data.finance.yf.Calendars", return_value=mock_cal):
            events = await get_earnings_calendar_upcoming()

        assert events == []

    @pytest.mark.asyncio
    async def test_handles_none(self):
        mock_cal = MagicMock()
        mock_cal.get_earnings_calendar.return_value = None

        with patch("src.mcp_servers.market_data.finance.yf.Calendars", return_value=mock_cal):
            events = await get_earnings_calendar_upcoming()

        assert events == []

    @pytest.mark.asyncio
    async def test_handles_exception(self):
        with patch(
            "src.mcp_servers.market_data.finance.yf.Calendars",
            side_effect=Exception("fail"),
        ):
            events = await get_earnings_calendar_upcoming()

        assert events == []


class TestGetTickerEarnings:
    @pytest.mark.asyncio
    async def test_returns_calendar_dict(self):
        mock_ticker = MagicMock()
        mock_ticker.calendar = {"Earnings Date": "2026-02-20", "EPS Estimate": 5.50}

        with patch("src.mcp_servers.market_data.finance.yf.Ticker", return_value=mock_ticker):
            result = await get_ticker_earnings("ASML.AS")

        assert result["ticker"] == "ASML.AS"
        assert result["earnings"]["Earnings Date"] == "2026-02-20"

    @pytest.mark.asyncio
    async def test_handles_none_calendar(self):
        mock_ticker = MagicMock()
        mock_ticker.calendar = None

        with patch("src.mcp_servers.market_data.finance.yf.Ticker", return_value=mock_ticker):
            result = await get_ticker_earnings("ASML.AS")

        assert result["ticker"] == "ASML.AS"
        assert result["earnings"] is None

    @pytest.mark.asyncio
    async def test_handles_exception(self):
        with patch("src.mcp_servers.market_data.finance.yf.Ticker", side_effect=Exception("fail")):
            result = await get_ticker_earnings("ASML.AS")

        assert result["ticker"] == "ASML.AS"
        assert result["earnings"] is None

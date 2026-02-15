from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from src.mcp_servers.market_data.finance import (
    compute_bollinger_bands,
    compute_ema,
    compute_macd,
    compute_moving_averages,
    compute_rsi,
    get_ticker_fundamentals,
    get_ticker_info,
    is_eu_market_open,
)

# --- EMA ---


class TestComputeEma:
    def test_basic(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = compute_ema(values, 3)
        assert len(result) == 3  # 5 - 3 + 1
        assert result[0] == pytest.approx(2.0)  # SMA of first 3

    def test_insufficient_data(self):
        assert compute_ema([1.0, 2.0], 5) == []


# --- RSI ---


class TestComputeRsi:
    def test_known_uptrend(self):
        # Steady uptrend should produce high RSI
        closes = [float(i) for i in range(1, 30)]
        rsi = compute_rsi(closes)
        assert rsi is not None
        assert rsi > 80

    def test_known_downtrend(self):
        # Steady downtrend should produce low RSI
        closes = [float(i) for i in range(30, 1, -1)]
        rsi = compute_rsi(closes)
        assert rsi is not None
        assert rsi < 20

    def test_insufficient_data(self):
        assert compute_rsi([1.0, 2.0, 3.0]) is None

    def test_rsi_bounds(self):
        # RSI is always 0-100
        closes = [10 + (i % 5) * 0.5 for i in range(50)]
        rsi = compute_rsi(closes)
        assert rsi is not None
        assert 0 <= rsi <= 100

    def test_flat_prices(self):
        # No movement = no losses => RSI 100
        closes = [10.0] * 20
        rsi = compute_rsi(closes)
        assert rsi == 100.0


# --- MACD ---


class TestComputeMacd:
    def test_insufficient_data(self):
        assert compute_macd([1.0] * 20) is None

    def test_returns_all_keys(self):
        closes = [float(i) for i in range(1, 50)]
        result = compute_macd(closes)
        assert result is not None
        assert "macd" in result
        assert "signal" in result
        assert "histogram" in result

    def test_histogram_is_diff(self):
        closes = [100 + i * 0.5 for i in range(60)]
        result = compute_macd(closes)
        assert result is not None
        assert result["histogram"] == pytest.approx(result["macd"] - result["signal"], abs=0.001)

    def test_uptrend_positive_macd(self):
        closes = [float(i) for i in range(1, 60)]
        result = compute_macd(closes)
        assert result is not None
        assert result["macd"] > 0


# --- Bollinger Bands ---


class TestComputeBollingerBands:
    def test_insufficient_data(self):
        assert compute_bollinger_bands([1.0] * 10) is None

    def test_returns_all_keys(self):
        closes = [float(i) for i in range(1, 30)]
        result = compute_bollinger_bands(closes)
        assert result is not None
        assert set(result.keys()) == {"upper", "middle", "lower", "bandwidth"}

    def test_upper_gt_middle_gt_lower(self):
        closes = [10 + i * 0.3 for i in range(30)]
        result = compute_bollinger_bands(closes)
        assert result is not None
        assert result["upper"] > result["middle"] > result["lower"]

    def test_bandwidth_formula(self):
        closes = [10 + i * 0.2 for i in range(25)]
        result = compute_bollinger_bands(closes)
        assert result is not None
        expected_bw = (result["upper"] - result["lower"]) / result["middle"]
        assert result["bandwidth"] == pytest.approx(expected_bw, abs=0.001)

    def test_flat_prices_zero_bandwidth(self):
        closes = [50.0] * 25
        result = compute_bollinger_bands(closes)
        assert result is not None
        assert result["upper"] == result["middle"] == result["lower"]
        assert result["bandwidth"] == 0.0


# --- Moving Averages ---


class TestComputeMovingAverages:
    def test_all_periods_with_enough_data(self):
        closes = [float(i) for i in range(1, 210)]
        result = compute_moving_averages(closes)
        for period in [10, 20, 50, 200]:
            assert result[f"sma_{period}"] is not None
            assert result[f"ema_{period}"] is not None

    def test_insufficient_data_returns_none(self):
        closes = [1.0, 2.0, 3.0]
        result = compute_moving_averages(closes)
        for period in [10, 20, 50, 200]:
            assert result[f"sma_{period}"] is None
            assert result[f"ema_{period}"] is None

    def test_sma_correctness(self):
        closes = [float(i) for i in range(1, 15)]
        result = compute_moving_averages(closes)
        # SMA_10 of last 10 values: [5,6,7,8,9,10,11,12,13,14] => avg = 9.5
        assert result["sma_10"] == pytest.approx(9.5)


# --- Market Status ---


class TestIsEuMarketOpen:
    def test_weekday_during_hours(self):
        # Wednesday 14:00 CET => open
        mock_dt = datetime(2026, 2, 11, 14, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        with patch("src.mcp_servers.market_data.finance.datetime") as mock:
            mock.now.return_value = mock_dt
            result = is_eu_market_open()
            assert result["is_open"] is True

    def test_weekday_before_open(self):
        # Wednesday 07:00 CET => closed
        mock_dt = datetime(2026, 2, 11, 7, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        with patch("src.mcp_servers.market_data.finance.datetime") as mock:
            mock.now.return_value = mock_dt
            result = is_eu_market_open()
            assert result["is_open"] is False

    def test_weekday_after_close(self):
        # Wednesday 20:00 CET => closed
        mock_dt = datetime(2026, 2, 11, 20, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        with patch("src.mcp_servers.market_data.finance.datetime") as mock:
            mock.now.return_value = mock_dt
            result = is_eu_market_open()
            assert result["is_open"] is False

    def test_weekend(self):
        # Saturday 12:00 CET => closed
        mock_dt = datetime(2026, 2, 14, 12, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        with patch("src.mcp_servers.market_data.finance.datetime") as mock:
            mock.now.return_value = mock_dt
            result = is_eu_market_open()
            assert result["is_open"] is False

    def test_returns_expected_keys(self):
        result = is_eu_market_open()
        assert "is_open" in result
        assert "current_time_cet" in result
        assert "weekday" in result
        assert "market_open" in result
        assert "market_close" in result


# --- yfinance wrappers (mocked) ---


class TestGetTickerInfo:
    @pytest.mark.asyncio
    async def test_returns_expected_fields(self):
        mock_info = {
            "shortName": "ASML Holding",
            "currentPrice": 850.0,
            "currency": "EUR",
            "exchange": "AMS",
            "dayHigh": 860.0,
            "dayLow": 840.0,
            "regularMarketChange": 5.0,
            "regularMarketChangePercent": 0.59,
            "volume": 1234567,
            "previousClose": 845.0,
        }
        mock_ticker = MagicMock()
        mock_ticker.info = mock_info

        with patch("src.mcp_servers.market_data.finance.yf.Ticker", return_value=mock_ticker):
            result = await get_ticker_info("ASML.AS")
            assert result["ticker"] == "ASML.AS"
            assert result["name"] == "ASML Holding"
            assert result["price"] == 850.0
            assert result["currency"] == "EUR"
            assert result["volume"] == 1234567

    @pytest.mark.asyncio
    async def test_fallback_to_regular_market_price(self):
        mock_info = {
            "shortName": "Test Corp",
            "currentPrice": None,
            "regularMarketPrice": 100.0,
            "currency": "EUR",
        }
        mock_ticker = MagicMock()
        mock_ticker.info = mock_info

        with patch("src.mcp_servers.market_data.finance.yf.Ticker", return_value=mock_ticker):
            result = await get_ticker_info("TEST.DE")
            assert result["price"] == 100.0


class TestGetTickerFundamentals:
    @pytest.mark.asyncio
    async def test_returns_expected_fields(self):
        mock_info = {
            "shortName": "SAP SE",
            "sector": "Technology",
            "industry": "Software",
            "marketCap": 250_000_000_000,
            "trailingPE": 28.5,
            "forwardPE": 25.0,
            "trailingEps": 6.8,
            "dividendYield": 0.012,
            "priceToBook": 5.5,
            "totalRevenue": 30_000_000_000,
            "profitMargins": 0.22,
            "debtToEquity": 45.0,
            "returnOnEquity": 0.18,
            "fiftyTwoWeekHigh": 220.0,
            "fiftyTwoWeekLow": 150.0,
        }
        mock_ticker = MagicMock()
        mock_ticker.info = mock_info

        with patch("src.mcp_servers.market_data.finance.yf.Ticker", return_value=mock_ticker):
            result = await get_ticker_fundamentals("SAP.DE")
            assert result["ticker"] == "SAP.DE"
            assert result["sector"] == "Technology"
            assert result["pe_ratio"] == 28.5
            assert result["market_cap"] == 250_000_000_000

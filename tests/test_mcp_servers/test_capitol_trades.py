import math
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.mcp_servers.market_data.capitol_trades import (
    _parse_value_range,
    _recency_decay,
    get_politician_candidates,
)

# ---------------------------------------------------------------------------
# _parse_value_range
# ---------------------------------------------------------------------------


class TestParseValueRange:
    def test_standard_stock_act_ranges(self):
        assert _parse_value_range("1001_15000") == (1_001, 15_000)
        assert _parse_value_range("15001_50000") == (15_001, 50_000)
        assert _parse_value_range("50001_100000") == (50_001, 100_000)
        assert _parse_value_range("100001_250000") == (100_001, 250_000)
        assert _parse_value_range("250001_500000") == (250_001, 500_000)
        assert _parse_value_range("500001_1000000") == (500_001, 1_000_000)
        assert _parse_value_range("1000001_5000000") == (1_000_001, 5_000_000)

    def test_generic_underscore_fallback(self):
        lo, hi = _parse_value_range("20000_80000")
        assert lo == 20_000
        assert hi == 80_000

    def test_empty_returns_zero(self):
        assert _parse_value_range("") == (0.0, 0.0)

    def test_unknown_string_returns_zero(self):
        assert _parse_value_range("unknown") == (0.0, 0.0)


# ---------------------------------------------------------------------------
# _recency_decay
# ---------------------------------------------------------------------------


class TestRecencyDecay:
    def test_today_is_one(self):
        assert _recency_decay(date.today()) == pytest.approx(1.0)

    def test_five_days_ago(self):
        d = date.today() - timedelta(days=5)
        assert _recency_decay(d) == pytest.approx(math.exp(-1.0))

    def test_none_returns_fallback(self):
        assert _recency_decay(None) == 0.5


# ---------------------------------------------------------------------------
# get_politician_candidates — mocked BFF API
# ---------------------------------------------------------------------------

_TODAY = date.today().isoformat()
_YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
_OLD = "2020-01-01"

_BFF_RESPONSE = {
    "data": [
        # AMD — 2 politicians buying (cluster)
        {
            "issuer": {"name": "Advanced Micro Devices Inc", "localTicker": "AMD"},
            "politician": {"firstName": "Nancy", "lastName": "Pelosi"},
            "txDate": _TODAY,
            "pubDate": _TODAY,
            "txType": "buy",
            "value": "50001_100000",
        },
        {
            "issuer": {"name": "Advanced Micro Devices Inc", "localTicker": "AMD"},
            "politician": {"firstName": "Dan", "lastName": "Goldman"},
            "txDate": _TODAY,
            "pubDate": _TODAY,
            "txType": "buy",
            "value": "15001_50000",
        },
        # NVDA — sell, should be excluded (txType filter double-check)
        {
            "issuer": {"name": "NVIDIA Corp", "localTicker": "NVDA"},
            "politician": {"firstName": "John", "lastName": "Doe"},
            "txDate": _TODAY,
            "pubDate": _TODAY,
            "txType": "sell",
            "value": "100001_250000",
        },
        # MSFT — old pub date, excluded by lookback
        {
            "issuer": {"name": "Microsoft Corp", "localTicker": "MSFT"},
            "politician": {"firstName": "Jane", "lastName": "Smith"},
            "txDate": _OLD,
            "pubDate": _OLD,
            "txType": "buy",
            "value": "50001_100000",
        },
    ]
}


def _make_mock_response(payload: dict) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


class TestGetPoliticianCandidates:
    @pytest.mark.asyncio
    async def test_filters_sells(self):
        mock_resp = _make_mock_response(_BFF_RESPONSE)
        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=10)
        tickers = [r["ticker"] for r in results]
        assert "NVDA" not in tickers

    @pytest.mark.asyncio
    async def test_filters_old_dates(self):
        mock_resp = _make_mock_response(_BFF_RESPONSE)
        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=10)
        tickers = [r["ticker"] for r in results]
        assert "MSFT" not in tickers

    @pytest.mark.asyncio
    async def test_groups_by_ticker(self):
        mock_resp = _make_mock_response(_BFF_RESPONSE)
        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=10)
        # AMD appears twice but should be grouped into one candidate
        amd_entries = [r for r in results if r["ticker"] == "AMD"]
        assert len(amd_entries) == 1

    @pytest.mark.asyncio
    async def test_cluster_flag(self):
        mock_resp = _make_mock_response(_BFF_RESPONSE)
        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=10)
        amd = next(r for r in results if r["ticker"] == "AMD")
        assert amd["is_cluster"] is True
        assert amd["insider_count"] == 2
        assert "Nancy Pelosi" in amd["insiders"]
        assert "Dan Goldman" in amd["insiders"]

    @pytest.mark.asyncio
    async def test_source_tag(self):
        mock_resp = _make_mock_response(_BFF_RESPONSE)
        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=10)
        for r in results:
            assert r["source"] == "capitol_trades"

    @pytest.mark.asyncio
    async def test_conviction_score(self):
        mock_resp = _make_mock_response(_BFF_RESPONSE)
        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=10)
        amd = next(r for r in results if r["ticker"] == "AMD")
        # Pelosi: 50001_100000 → midpoint 75000.5, today → decay=1.0 → conviction ≈ 75000.5
        # Goldman: 15001_50000 → midpoint 32501.0, today → decay=1.0 → conviction ≈ 32501.0
        # Combined ≈ 107501.5
        assert amd["conviction_score"] == pytest.approx(107_501.5, rel=0.01)

    @pytest.mark.asyncio
    async def test_top_n_limit(self):
        data = [
            {
                "issuer": {"name": f"Company {i}", "localTicker": f"T{i}"},
                "politician": {"firstName": "A", "lastName": "B"},
                "txDate": _TODAY,
                "pubDate": _TODAY,
                "txType": "buy",
                "value": "15001_50000",
            }
            for i in range(10)
        ]
        mock_resp = _make_mock_response({"data": data})
        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=3)
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        import httpx

        with patch(
            "src.mcp_servers.market_data.capitol_trades.httpx.get",
            side_effect=httpx.HTTPStatusError(
                "403", request=MagicMock(), response=MagicMock(status_code=403, text="Forbidden")
            ),
        ):
            results = await get_politician_candidates(lookback_days=3, top_n=10)
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_data_returns_empty(self):
        mock_resp = _make_mock_response({"data": []})
        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=10)
        assert results == []

    @pytest.mark.asyncio
    async def test_candidate_shape(self):
        """Verify all fields expected by _enrich_candidate are present."""
        mock_resp = _make_mock_response(_BFF_RESPONSE)
        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=10)
        required = {
            "ticker",
            "company",
            "source",
            "insider_count",
            "insiders",
            "is_cluster",
            "is_csuite_present",
            "total_value_usd",
            "conviction_score",
            "max_delta_own_pct",
            "transactions",
        }
        for r in results:
            assert required.issubset(r.keys())
            assert r["is_csuite_present"] is False
            assert r["max_delta_own_pct"] == 0.0

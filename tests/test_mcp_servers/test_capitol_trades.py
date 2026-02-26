import math
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.mcp_servers.market_data.capitol_trades import (
    _parse_trade_size,
    _recency_decay,
    get_politician_candidates,
)

# ---------------------------------------------------------------------------
# _parse_trade_size
# ---------------------------------------------------------------------------


class TestParseTradeSize:
    def test_k_range(self):
        lo, hi = _parse_trade_size("5K\u201315K")  # en-dash
        assert lo == 5_000
        assert hi == 15_000

    def test_k_to_m_range(self):
        lo, hi = _parse_trade_size("500K\u20131M")
        assert lo == 500_000
        assert hi == 1_000_000

    def test_m_range(self):
        lo, hi = _parse_trade_size("1M\u20135M")
        assert lo == 1_000_000
        assert hi == 5_000_000

    def test_hyphen_separator(self):
        lo, hi = _parse_trade_size("50K-100K")
        assert lo == 50_000
        assert hi == 100_000

    def test_large_k_range(self):
        lo, hi = _parse_trade_size("100K\u2013250K")
        assert lo == 100_000
        assert hi == 250_000

    def test_single_value_fallback(self):
        lo, hi = _parse_trade_size("50K")
        assert lo == 50_000
        assert hi == 50_000

    def test_empty_string(self):
        lo, hi = _parse_trade_size("")
        assert lo == 0.0
        assert hi == 0.0


# ---------------------------------------------------------------------------
# _recency_decay
# ---------------------------------------------------------------------------


class TestRecencyDecay:
    def test_today_is_max(self):
        decay = _recency_decay(date.today())
        assert decay == pytest.approx(1.0)

    def test_five_days_ago(self):
        from datetime import timedelta

        d = date.today() - timedelta(days=5)
        expected = math.exp(-0.2 * 5)
        assert _recency_decay(d) == pytest.approx(expected)

    def test_none_returns_fallback(self):
        assert _recency_decay(None) == 0.5


# ---------------------------------------------------------------------------
# get_politician_candidates — mocked HTTP
# ---------------------------------------------------------------------------

# Minimal HTML with:
# - AMD: 2 politician buys (cluster)
# - NVDA: 1 sell (should be excluded)
# - MSFT: 1 buy, old date (should be excluded by lookback filter)
_SAMPLE_HTML = """
<html><body>
<table>
  <tr class="q-tr">
    <td>
      <span class="q-field issuer-ticker">AMD</span>
      <h3 class="q-field issuer-name">Advanced Micro Devices</h3>
    </td>
    <td><h3 class="q-fieldset politician-name">Nancy Pelosi</h3></td>
    <td><span class="q-field trade-size">50K\u2013100K</span></td>
    <td><div class="q-cell cell--pub-date">{today}</div></td>
    <td><span class="tx-type--buy">Buy</span></td>
  </tr>
  <tr class="q-tr">
    <td>
      <span class="q-field issuer-ticker">AMD</span>
      <h3 class="q-field issuer-name">Advanced Micro Devices</h3>
    </td>
    <td><h3 class="q-fieldset politician-name">Dan Goldman</h3></td>
    <td><span class="q-field trade-size">15K\u201350K</span></td>
    <td><div class="q-cell cell--pub-date">{today}</div></td>
    <td><span class="tx-type--buy">Buy</span></td>
  </tr>
  <tr class="q-tr">
    <td>
      <span class="q-field issuer-ticker">NVDA</span>
      <h3 class="q-field issuer-name">NVIDIA Corp</h3>
    </td>
    <td><h3 class="q-fieldset politician-name">John Doe</h3></td>
    <td><span class="q-field trade-size">100K\u2013250K</span></td>
    <td><div class="q-cell cell--pub-date">{today}</div></td>
    <td><span class="tx-type--sell">Sell</span></td>
  </tr>
  <tr class="q-tr">
    <td>
      <span class="q-field issuer-ticker">MSFT</span>
      <h3 class="q-field issuer-name">Microsoft Corp</h3>
    </td>
    <td><h3 class="q-fieldset politician-name">Jane Smith</h3></td>
    <td><span class="q-field trade-size">50K\u2013100K</span></td>
    <td><div class="q-cell cell--pub-date">2020-01-01</div></td>
    <td><span class="tx-type--buy">Buy</span></td>
  </tr>
</table>
</body></html>
"""


def _make_mock_response(html: str) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


class TestGetPoliticianCandidates:
    @pytest.mark.asyncio
    async def test_returns_only_buys(self):
        html = _SAMPLE_HTML.format(today=date.today().isoformat())
        mock_resp = _make_mock_response(html)

        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=10)

        tickers = [r["ticker"] for r in results]
        assert "NVDA" not in tickers  # sell row filtered out
        assert "MSFT" not in tickers  # old date filtered out
        assert "AMD" in tickers

    @pytest.mark.asyncio
    async def test_groups_by_ticker(self):
        html = _SAMPLE_HTML.format(today=date.today().isoformat())
        mock_resp = _make_mock_response(html)

        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=10)

        # AMD appears twice in HTML but should be grouped into one candidate
        assert len([r for r in results if r["ticker"] == "AMD"]) == 1

    @pytest.mark.asyncio
    async def test_cluster_flag(self):
        html = _SAMPLE_HTML.format(today=date.today().isoformat())
        mock_resp = _make_mock_response(html)

        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=10)

        amd = next(r for r in results if r["ticker"] == "AMD")
        assert amd["is_cluster"] is True
        assert amd["insider_count"] == 2
        assert "Nancy Pelosi" in amd["insiders"]
        assert "Dan Goldman" in amd["insiders"]

    @pytest.mark.asyncio
    async def test_source_tag(self):
        html = _SAMPLE_HTML.format(today=date.today().isoformat())
        mock_resp = _make_mock_response(html)

        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=10)

        for r in results:
            assert r["source"] == "capitol_trades"

    @pytest.mark.asyncio
    async def test_conviction_score_is_sum(self):
        html = _SAMPLE_HTML.format(today=date.today().isoformat())
        mock_resp = _make_mock_response(html)

        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=10)

        amd = next(r for r in results if r["ticker"] == "AMD")
        # AMD: Pelosi 50K–100K → midpoint 75K, Goldman 15K–50K → midpoint 32.5K
        # Both today → recency_decay = 1.0
        # Combined conviction ≈ 75000 + 32500 = 107500
        assert amd["conviction_score"] == pytest.approx(107_500.0, rel=0.01)
        assert amd["total_value_usd"] == pytest.approx(107_500.0, rel=0.01)

    @pytest.mark.asyncio
    async def test_top_n_limit(self):
        # Build HTML with 5 unique tickers all bought today
        today = date.today().isoformat()
        rows = ""
        for i, ticker in enumerate(["A1", "B2", "C3", "D4", "E5"]):
            rows += f"""
  <tr class="q-tr">
    <td>
      <span class="q-field issuer-ticker">{ticker}</span>
      <h3 class="q-field issuer-name">Company {i}</h3>
    </td>
    <td><h3 class="q-fieldset politician-name">Politician {i}</h3></td>
    <td><span class="q-field trade-size">50K\u2013100K</span></td>
    <td><div class="q-cell cell--pub-date">{today}</div></td>
    <td><span class="tx-type--buy">Buy</span></td>
  </tr>"""
        html = f"<html><body><table>{rows}</table></body></html>"
        mock_resp = _make_mock_response(html)

        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=3)

        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        import httpx

        with patch(
            "src.mcp_servers.market_data.capitol_trades.httpx.get",
            side_effect=httpx.HTTPStatusError(
                "403", request=MagicMock(), response=MagicMock(status_code=403)
            ),
        ):
            results = await get_politician_candidates(lookback_days=3, top_n=10)

        assert results == []

    @pytest.mark.asyncio
    async def test_empty_page_returns_empty(self):
        mock_resp = _make_mock_response("<html><body><table></table></body></html>")

        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=10)

        assert results == []

    @pytest.mark.asyncio
    async def test_candidate_shape(self):
        """Verify returned candidates have all fields _enrich_candidate expects."""
        html = _SAMPLE_HTML.format(today=date.today().isoformat())
        mock_resp = _make_mock_response(html)

        with patch("src.mcp_servers.market_data.capitol_trades.httpx.get", return_value=mock_resp):
            results = await get_politician_candidates(lookback_days=3, top_n=10)

        required_keys = {
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
            assert required_keys.issubset(r.keys()), f"Missing keys in {r['ticker']}"
            assert r["is_csuite_present"] is False
            assert r["max_delta_own_pct"] == 0.0

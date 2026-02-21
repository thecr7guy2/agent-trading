from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import DailyPicks, LLMProvider, StockPick
from src.orchestrator.supervisor import (
    PipelineResult,
    Supervisor,
    _is_valid_stock_ticker,
    _select_candidates,
)


class _MockMCPClient:
    def __init__(self, responses: dict | None = None):
        self._responses = responses or {}
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        return self._responses.get(name, {"status": "ok"})

    async def close(self) -> None:
        pass


def _settings():
    return SimpleNamespace(
        orchestrator_timezone="Europe/Berlin",
        daily_budget_eur=10.0,
        practice_daily_budget_eur=500.0,
        t212_api_key="live-key",
        t212_api_secret="",
        t212_practice_api_key="demo-key",
        t212_practice_api_secret="",
        telegram_enabled=False,
        telegram_bot_token=None,
        telegram_chat_id=None,
        sell_stop_loss_pct=10.0,
        sell_take_profit_pct=15.0,
        sell_max_hold_days=5,
        sell_check_schedule="09:30,12:30,16:45",
        max_candidates=15,
        recently_traded_path="recently_traded.json",
        recently_traded_days=3,
        pipeline_timeout_seconds=600,
    )


def _daily_picks(llm: LLMProvider) -> DailyPicks:
    return DailyPicks(
        llm=llm,
        pick_date=date(2026, 2, 18),
        picks=[StockPick(ticker="ASML.AS", allocation_pct=100.0, action="buy")],
        confidence=0.8,
        market_summary="test",
    )


class TestSupervisor:
    @pytest.mark.asyncio
    async def test_run_decision_cycle_skips_weekend(self):
        supervisor = Supervisor(settings=_settings())
        result = await supervisor.run_decision_cycle(run_date=date(2026, 2, 15))  # Saturday
        assert result["status"] == "skipped"
        assert result["reason"] == "non-trading-day"

    @pytest.mark.asyncio
    async def test_run_decision_cycle_happy_path(self):
        supervisor = Supervisor(settings=_settings())
        supervisor.build_signal_digest = AsyncMock(
            return_value={
                "total_posts": 42,
                "candidates": [{"ticker": "ASML.AS", "sources": ["reddit"]}],
                "source_type": "multi",
            }
        )
        supervisor._run_pipelines = AsyncMock(
            return_value=[
                PipelineResult(
                    llm=LLMProvider.CLAUDE,
                    picks=_daily_picks(LLMProvider.CLAUDE),
                    research=None,
                    portfolio=[],
                ),
            ]
        )
        supervisor._picks_to_candidates = AsyncMock(return_value=[])

        mock_summary = MagicMock()
        mock_summary.bought = []
        mock_summary.failed = []

        with patch("src.orchestrator.supervisor.execute_with_fallback", AsyncMock(return_value=mock_summary)):
            with patch("src.orchestrator.supervisor.get_blacklist", return_value=set()):
                result = await supervisor.run_decision_cycle(run_date=date(2026, 2, 18))

        assert result["status"] == "ok"
        assert result["conservative_trader"] == "claude"
        assert result["reddit_posts"] == 42

    @pytest.mark.asyncio
    async def test_run_decision_cycle_filters_blacklist(self):
        supervisor = Supervisor(settings=_settings())
        supervisor.build_signal_digest = AsyncMock(
            return_value={
                "total_posts": 10,
                "candidates": [
                    {"ticker": "NVDA", "sources": ["reddit"]},
                    {"ticker": "ASML.AS", "sources": ["reddit"]},
                ],
                "source_type": "multi",
            }
        )
        supervisor._run_pipelines = AsyncMock(
            return_value=[
                PipelineResult(
                    llm=LLMProvider.CLAUDE,
                    picks=_daily_picks(LLMProvider.CLAUDE),
                    research=None,
                    portfolio=[],
                ),
            ]
        )
        supervisor._picks_to_candidates = AsyncMock(return_value=[])

        mock_summary = MagicMock()
        mock_summary.bought = []
        mock_summary.failed = []

        # NVDA is blacklisted
        with patch("src.orchestrator.supervisor.execute_with_fallback", AsyncMock(return_value=mock_summary)):
            with patch("src.orchestrator.supervisor.get_blacklist", return_value={"NVDA"}):
                result = await supervisor.run_decision_cycle(run_date=date(2026, 2, 18))

        # Only ASML.AS should remain after filtering
        assert result["tickers_analyzed"] == 1

    @pytest.mark.asyncio
    async def test_run_end_of_day(self):
        supervisor = Supervisor(settings=_settings())
        mock_t212 = AsyncMock()
        live_positions = [
            {"ticker": "ASML.AS", "quantity": 0.5, "avg_buy_price": 850.0, "current_price": 900.0}
        ]

        with patch("src.orchestrator.supervisor.get_live_positions", AsyncMock(return_value=live_positions)):
            with patch("src.orchestrator.supervisor.get_demo_positions", AsyncMock(return_value=[])):
                supervisor._get_t212_live = MagicMock(return_value=mock_t212)
                supervisor._get_t212_demo = MagicMock(return_value=mock_t212)
                result = await supervisor.run_end_of_day(run_date=date(2026, 2, 18))

        assert result["status"] == "ok"
        assert result["date"] == "2026-02-18"
        assert "conservative_real" in result["snapshots"]
        snap = result["snapshots"]["conservative_real"]
        # invested = 0.5 * 850 = 425, value = 0.5 * 900 = 450, pnl = 25
        assert snap["total_invested"] == "425.00"
        assert snap["total_value"] == "450.00"
        assert snap["unrealized_pnl"] == "25.00"

    @pytest.mark.asyncio
    async def test_run_end_of_day_no_demo(self):
        """When no practice key is configured, only live snapshot is returned."""
        settings = _settings()
        settings.t212_practice_api_key = None
        supervisor = Supervisor(settings=settings)
        mock_t212 = AsyncMock()

        with patch("src.orchestrator.supervisor.get_live_positions", AsyncMock(return_value=[])):
            supervisor._get_t212_live = MagicMock(return_value=mock_t212)
            result = await supervisor.run_end_of_day(run_date=date(2026, 2, 18))

        assert result["status"] == "ok"
        assert "conservative_real" in result["snapshots"]
        assert "aggressive_demo" not in result["snapshots"]


class TestHelpers:
    def test_is_valid_ticker_filters_noise(self):
        assert not _is_valid_stock_ticker("DD")
        assert not _is_valid_stock_ticker("CEO")
        assert not _is_valid_stock_ticker("ETF")
        assert not _is_valid_stock_ticker("SPY")
        assert not _is_valid_stock_ticker("AB")  # 2 chars

    def test_is_valid_ticker_allows_normal(self):
        assert _is_valid_stock_ticker("ASML.AS")
        assert _is_valid_stock_ticker("SAP.DE")
        assert _is_valid_stock_ticker("AAPL")
        assert _is_valid_stock_ticker("NVO")

    def test_select_candidates_respects_limit(self):
        candidates = {
            str(i): {"ticker": str(i), "sources": ["reddit"], "reddit_mentions": i}
            for i in range(20)
        }
        result = _select_candidates(candidates, limit=10)
        assert len(result) <= 10

    def test_select_candidates_multi_source_first(self):
        candidates = {
            "A": {"ticker": "A", "sources": ["reddit", "screener"], "reddit_mentions": 1},
            "B": {"ticker": "B", "sources": ["reddit"], "reddit_mentions": 100},
        }
        result = _select_candidates(candidates, limit=5)
        # Multi-source should come before single-source reddit
        assert result[0]["ticker"] == "A"

    def test_select_candidates_empty(self):
        result = _select_candidates({}, limit=10)
        assert result == []

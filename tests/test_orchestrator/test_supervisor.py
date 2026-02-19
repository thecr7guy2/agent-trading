from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.db.models import DailyPicks, LLMProvider, Position, StockPick
from src.orchestrator.approval import ApprovalDecision
from src.orchestrator.supervisor import PipelineResult, Supervisor


class _AutoApprove:
    async def request(self, picks: DailyPicks) -> ApprovalDecision:
        return ApprovalDecision(
            action="approve_all",
            approved_indices=list(range(len(picks.picks))),
        )


class _MockMCPClient:
    def __init__(self, responses: dict[str, dict | list] | None = None):
        self._responses = responses or {}
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        resp = self._responses.get(name, {"status": "filled"})
        if callable(resp):
            return resp(name, arguments)
        return resp

    async def close(self) -> None:
        pass


def _settings():
    return SimpleNamespace(
        approval_timeout_seconds=120,
        approval_timeout_action="approve_all",
        market_data_ticker_limit=12,
        orchestrator_timezone="Europe/Berlin",
        daily_budget_eur=10.0,
        scheduler_eod_time="17:35",
        sell_stop_loss_pct=10.0,
        sell_take_profit_pct=15.0,
        sell_max_hold_days=5,
        sell_check_schedule="09:30,12:30,16:45",
        telegram_enabled=False,
        telegram_bot_token=None,
        telegram_chat_id=None,
        signal_candidate_limit=25,
        screener_min_market_cap=1_000_000_000,
        screener_exchanges="AMS,PAR,GER,MIL,MCE,LSE",
        max_tool_rounds=8,
        pipeline_timeout_seconds=600,
    )


def _daily_picks(llm: LLMProvider) -> DailyPicks:
    return DailyPicks(
        llm=llm,
        pick_date=date(2026, 2, 16),
        picks=[StockPick(ticker="ASML.AS", allocation_pct=100.0, action="buy")],
        confidence=0.8,
        market_summary="test",
    )


def _positions(llm: LLMProvider, is_real: bool = False) -> list[Position]:
    return [
        Position(
            id=1,
            llm_name=llm,
            ticker="ASML.AS",
            quantity=Decimal("0.5"),
            avg_buy_price=Decimal("850"),
            is_real=is_real,
        )
    ]


class TestSupervisor:
    @pytest.mark.asyncio
    async def test_run_decision_cycle_happy_path(self):
        mock_trading = _MockMCPClient({"place_buy_order": {"status": "filled"}})
        supervisor = Supervisor(
            settings=_settings(),
            approval_flow=_AutoApprove(),
            trading_client=mock_trading,
        )
        supervisor.build_reddit_digest = AsyncMock(
            return_value={"total_posts": 42, "tickers": [{"ticker": "ASML.AS"}]}
        )
        supervisor._run_pipelines = AsyncMock(
            return_value=[
                PipelineResult(
                    llm=LLMProvider.CLAUDE,
                    picks=_daily_picks(LLMProvider.CLAUDE),
                    research=None,
                    portfolio=[],
                ),
                PipelineResult(
                    llm=LLMProvider.MINIMAX,
                    picks=_daily_picks(LLMProvider.MINIMAX),
                    research=None,
                    portfolio=[],
                ),
            ]
        )
        supervisor._get_portfolio_manager = AsyncMock(
            return_value=AsyncMock(
                save_daily_picks=AsyncMock(),
                trade_exists=AsyncMock(return_value=False),
            )
        )
        supervisor._execute_real_trades = AsyncMock(return_value=[{"status": "filled"}])
        supervisor._execute_virtual_trades = AsyncMock(return_value=[{"status": "filled"}])
        supervisor._persist_sentiment = AsyncMock()

        result = await supervisor.run_decision_cycle(run_date=date(2026, 2, 16))

        assert result["status"] == "ok"
        assert result["main_trader"] == "claude"
        assert result["virtual_trader"] == "minimax"
        assert result["reddit_posts"] == 42
        supervisor._execute_real_trades.assert_awaited_once()
        supervisor._execute_virtual_trades.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_decision_cycle_skips_weekend(self):
        supervisor = Supervisor(settings=_settings(), approval_flow=_AutoApprove())
        result = await supervisor.run_decision_cycle(run_date=date(2026, 2, 15))
        assert result["status"] == "skipped"
        assert result["reason"] == "non-trading-day"

    @pytest.mark.asyncio
    async def test_execute_real_trades_duplicate_guard(self):
        mock_trading = _MockMCPClient({"place_buy_order": {"status": "filled"}})
        mock_market = _MockMCPClient({"get_stock_price": {"price": 850.0}})
        supervisor = Supervisor(
            settings=_settings(),
            approval_flow=_AutoApprove(),
            trading_client=mock_trading,
            market_data_client=mock_market,
        )
        picks = _daily_picks(LLMProvider.CLAUDE)

        mock_pm = AsyncMock()
        mock_pm.trade_exists = AsyncMock(return_value=True)
        supervisor._get_portfolio_manager = AsyncMock(return_value=mock_pm)

        result = await supervisor._execute_real_trades(
            llm=LLMProvider.CLAUDE,
            picks=picks,
            budget_eur=10.0,
            portfolio=[],
            force=False,
        )
        assert result[0]["status"] == "skipped"
        assert result[0]["reason"] == "duplicate"
        assert len(mock_trading.calls) == 0

    @pytest.mark.asyncio
    async def test_execute_real_trades_force_bypasses_duplicate_guard(self):
        mock_trading = _MockMCPClient({"place_buy_order": {"status": "filled"}})
        mock_market = _MockMCPClient({"get_stock_price": {"price": 850.0}})
        supervisor = Supervisor(
            settings=_settings(),
            approval_flow=_AutoApprove(),
            trading_client=mock_trading,
            market_data_client=mock_market,
        )
        picks = _daily_picks(LLMProvider.CLAUDE)

        mock_pm = AsyncMock()
        mock_pm.trade_exists = AsyncMock(return_value=True)
        supervisor._get_portfolio_manager = AsyncMock(return_value=mock_pm)

        result = await supervisor._execute_real_trades(
            llm=LLMProvider.CLAUDE,
            picks=picks,
            budget_eur=10.0,
            portfolio=[],
            force=True,
        )
        assert result[0]["status"] == "filled"
        assert len(mock_trading.calls) == 1
        assert mock_trading.calls[0][0] == "place_buy_order"
        assert mock_trading.calls[0][1]["current_price"] == 850.0

    @pytest.mark.asyncio
    async def test_execute_virtual_trade_skips_missing_price(self):
        mock_trading = _MockMCPClient({"record_virtual_trade": {"status": "filled"}})
        mock_market = _MockMCPClient({"get_stock_price": {}})
        supervisor = Supervisor(
            settings=_settings(),
            approval_flow=_AutoApprove(),
            trading_client=mock_trading,
            market_data_client=mock_market,
        )
        picks = _daily_picks(LLMProvider.MINIMAX)

        mock_pm = AsyncMock()
        mock_pm.trade_exists = AsyncMock(return_value=False)
        supervisor._get_portfolio_manager = AsyncMock(return_value=mock_pm)

        result = await supervisor._execute_virtual_trades(
            llm=LLMProvider.MINIMAX,
            picks=picks,
            budget_eur=10.0,
            portfolio=[],
            force=False,
        )
        assert result[0]["status"] == "skipped"
        assert result[0]["reason"] == "missing_price"
        assert len(mock_trading.calls) == 0

    def test_normalize_allocations(self):
        supervisor = Supervisor(settings=_settings(), approval_flow=_AutoApprove())
        picks = DailyPicks(
            llm=LLMProvider.CLAUDE,
            pick_date=date(2026, 2, 16),
            picks=[
                StockPick(ticker="ASML.AS", allocation_pct=80.0, action="buy"),
                StockPick(ticker="SAP.DE", allocation_pct=40.0, action="buy"),
            ],
        )
        supervisor._normalize_allocations(picks)
        total = sum(pick.allocation_pct for pick in picks.picks)
        assert total == pytest.approx(100.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_run_end_of_day(self):
        mock_market = _MockMCPClient({"get_stock_price": {"price": 900.0}})
        supervisor = Supervisor(
            settings=_settings(),
            approval_flow=_AutoApprove(),
            market_data_client=mock_market,
        )

        mock_pm = AsyncMock()
        mock_pm.get_positions_typed = AsyncMock(
            return_value=_positions(LLMProvider.CLAUDE, is_real=False)
        )
        mock_pm.calculate_pnl = AsyncMock(return_value={"realized_pnl": "5"})
        mock_pm.save_portfolio_snapshot = AsyncMock()
        supervisor._get_portfolio_manager = AsyncMock(return_value=mock_pm)

        result = await supervisor.run_end_of_day(run_date=date(2026, 2, 16))

        assert result["status"] == "ok"
        assert result["date"] == "2026-02-16"
        assert mock_pm.save_portfolio_snapshot.call_count == 4  # 2 LLMs x 2 (real/virtual)

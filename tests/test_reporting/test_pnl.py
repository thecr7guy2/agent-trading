from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.db.models import LLMProvider, Position
from src.reporting.pnl import PnLEngine


class _MockMarketClient:
    def __init__(self, prices: dict[str, float] | None = None):
        self._prices = prices or {}

    async def call_tool(self, name: str, arguments: dict) -> dict:
        if name == "get_stock_price":
            ticker = arguments.get("ticker", "")
            return {"price": self._prices.get(ticker, 0.0)}
        return {}

    async def close(self) -> None:
        pass


def _mock_pm(
    pnl_data: dict | None = None,
    positions: list[Position] | None = None,
    trade_history: list[dict] | None = None,
):
    pm = AsyncMock()
    pm.calculate_pnl.return_value = pnl_data or {
        "llm_name": "claude",
        "period_start": "2026-02-10",
        "period_end": "2026-02-14",
        "total_invested": "50.00",
        "total_proceeds": "0",
        "realized_pnl": "0",
        "total_sell_trades": 0,
        "win_count": 0,
        "loss_count": 0,
        "win_rate": 0.0,
    }
    pm.get_positions_typed.return_value = positions or []
    pm.get_trade_history.return_value = trade_history or []
    return pm


@pytest.mark.asyncio
async def test_get_pnl_report():
    positions = [
        Position(
            llm_name=LLMProvider.CLAUDE,
            ticker="ASML.AS",
            quantity=Decimal("2"),
            avg_buy_price=Decimal("10.00"),
            is_real=True,
        )
    ]
    pm = _mock_pm(
        pnl_data={
            "llm_name": "claude",
            "period_start": "2026-02-10",
            "period_end": "2026-02-14",
            "total_invested": "20.00",
            "total_proceeds": "0",
            "realized_pnl": "0",
            "total_sell_trades": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
        },
        positions=positions,
    )
    market = _MockMarketClient({"ASML.AS": 11.0})
    engine = PnLEngine(pm, market)

    report = await engine.get_pnl_report(
        LLMProvider.CLAUDE, date(2026, 2, 10), date(2026, 2, 14), is_real=True
    )

    assert report.llm_name == LLMProvider.CLAUDE
    assert report.total_invested == Decimal("20.00")
    assert report.unrealized_pnl == Decimal("2")  # 2 shares * (11-10)
    assert report.total_pnl == Decimal("2")
    assert report.return_pct == 10.0


@pytest.mark.asyncio
async def test_get_pnl_report_no_positions():
    pm = _mock_pm()
    market = _MockMarketClient()
    engine = PnLEngine(pm, market)

    report = await engine.get_pnl_report(
        LLMProvider.CLAUDE, date(2026, 2, 10), date(2026, 2, 14), is_real=True
    )

    assert report.unrealized_pnl == Decimal("0")
    assert report.total_pnl == Decimal("0")


@pytest.mark.asyncio
async def test_get_best_worst_picks():
    trades = [
        {
            "ticker": "ASML.AS",
            "action": "buy",
            "trade_date": "2026-02-12",
            "price_per_share": "10.00",
        },
        {
            "ticker": "SAP.DE",
            "action": "buy",
            "trade_date": "2026-02-13",
            "price_per_share": "20.00",
        },
    ]
    pm = _mock_pm(trade_history=trades)
    market = _MockMarketClient({"ASML.AS": 12.0, "SAP.DE": 18.0})
    engine = PnLEngine(pm, market)

    result = await engine.get_best_worst_picks(date(2026, 2, 10), date(2026, 2, 14))

    assert result["best"]["ticker"] == "ASML.AS"
    assert result["best"]["return_pct"] == 20.0
    assert result["worst"]["ticker"] == "SAP.DE"
    assert result["worst"]["return_pct"] == -10.0


@pytest.mark.asyncio
async def test_get_portfolio_summary():
    positions = [
        Position(
            llm_name=LLMProvider.CLAUDE,
            ticker="ASML.AS",
            quantity=Decimal("1"),
            avg_buy_price=Decimal("10.00"),
            is_real=True,
        )
    ]
    pm = _mock_pm(positions=positions)
    # get_portfolio_summary loops over all LLMProviders; return positions only for claude
    pm.get_positions_typed.side_effect = lambda llm_name: positions if llm_name == "claude" else []
    market = _MockMarketClient({"ASML.AS": 12.0})
    engine = PnLEngine(pm, market)

    summary = await engine.get_portfolio_summary(is_real=True)

    assert float(summary["total_invested"]) == 10.0
    assert float(summary["total_value"]) == 12.0
    assert float(summary["pnl"]) == 2.0
    assert summary["return_pct"] == 20.0

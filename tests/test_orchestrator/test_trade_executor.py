from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.orchestrator.supervisor import Supervisor
from src.orchestrator.trade_executor import execute_with_fallback


class _MockT212:
    def __init__(self, order_response: dict):
        self._order_response = order_response

    async def get_account_cash(self) -> dict:
        return {"free": 10_000.0}

    async def resolve_ticker(self, ticker: str) -> str:
        return f"{ticker}_US_EQ"

    async def place_market_order(self, ticker: str, quantity: float) -> dict:
        return self._order_response


@pytest.mark.asyncio
async def test_execute_with_fallback_uses_requested_amount_when_fill_fields_are_zero(monkeypatch):
    monkeypatch.setattr(
        "src.orchestrator.trade_executor.get_settings",
        lambda: SimpleNamespace(
            budget_per_run_eur=1000.0,
            recently_traded_path="recently_traded.json",
            recently_traded_days=3,
        ),
    )
    monkeypatch.setattr("src.orchestrator.trade_executor.add_many", lambda *args, **kwargs: None)

    summary = await execute_with_fallback(
        candidates=[{"ticker": "ASML", "price": 100.0, "allocation_pct": 100.0}],
        t212=_MockT212(order_response={"id": "ord-1", "filledQuantity": 0, "filledValue": 0}),
    )

    assert summary.num_bought == 1
    assert summary.total_spent == pytest.approx(1000.0)
    assert summary.bought[0].quantity == pytest.approx(10.0)
    assert summary.bought[0].amount_spent == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_run_decision_cycle_skips_when_portfolio_cap_is_reached(monkeypatch):
    settings = SimpleNamespace(
        orchestrator_timezone="Europe/Berlin",
        t212_api_key="demo-key",
        t212_api_secret="",
        max_demo_portfolio_invested_eur=46_000.0,
    )
    supervisor = Supervisor(settings=settings)
    supervisor._get_t212 = lambda: object()
    supervisor.build_insider_digest = AsyncMock(return_value={"insider_count": 0, "candidates": []})

    monkeypatch.setattr(
        "src.orchestrator.supervisor.get_demo_positions",
        AsyncMock(
            return_value=[{"ticker": "ASML", "quantity": 10.0, "avg_buy_price": 5000.0, "current_price": 5100.0}]
        ),
    )

    result = await supervisor.run_decision_cycle(force=True)

    assert result["status"] == "skipped"
    assert "cap reached" in result["reason"]
    supervisor.build_insider_digest.assert_not_awaited()


def test_resolve_portfolio_totals_prefers_account_cash():
    positions = [
        {"ticker": "X", "quantity": 10.0, "avg_buy_price": 1000.0, "current_price": 1100.0},
    ]
    invested, value, pnl = Supervisor._resolve_portfolio_totals(
        positions=positions,
        account_cash={"invested": 30_918.8, "ppl": -42.23},
    )
    assert invested == pytest.approx(30_918.8)
    assert value == pytest.approx(30_876.57)
    assert pnl == pytest.approx(-42.23)


def test_resolve_portfolio_totals_falls_back_to_positions():
    positions = [
        {"ticker": "X", "quantity": 2.0, "avg_buy_price": 50.0, "current_price": 60.0},
    ]
    invested, value, pnl = Supervisor._resolve_portfolio_totals(
        positions=positions,
        account_cash={"invested": 0.0, "ppl": 0.0},
    )
    assert invested == pytest.approx(100.0)
    assert value == pytest.approx(120.0)
    assert pnl == pytest.approx(20.0)

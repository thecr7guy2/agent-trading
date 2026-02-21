from datetime import date
from decimal import Decimal

import pytest

from src.config import Settings
from src.models import LLMProvider, Position
from src.orchestrator.sell_strategy import SellStrategyEngine


@pytest.fixture
def settings():
    return Settings(
        anthropic_api_key="test",
        minimax_api_key="test",
        t212_api_key="test",
        t212_api_secret="test",
        sell_stop_loss_pct=10.0,
        sell_take_profit_pct=15.0,
        sell_max_hold_days=5,
    )


@pytest.fixture
def engine(settings):
    return SellStrategyEngine(settings)


@pytest.fixture
def position():
    return Position(
        llm_name=LLMProvider.CLAUDE,
        ticker="ASML.AS",
        quantity=Decimal("0.05"),
        avg_buy_price=Decimal("700.00"),
        is_real=True,
        opened_at=date(2025, 2, 10),
    )


class TestStopLoss:
    def test_triggers_at_threshold(self, engine, position):
        # Price dropped 11.4%: 700 -> 620
        signal = engine.evaluate_position(position, 620.0, date(2025, 2, 11))
        assert signal is not None
        assert signal.signal_type == "stop_loss"
        assert signal.return_pct < -10.0

    def test_does_not_trigger_below_threshold(self, engine, position):
        # Price dropped 5%: 700 -> 665
        signal = engine.evaluate_position(position, 665.0, date(2025, 2, 11))
        assert signal is None

    def test_triggers_at_exact_threshold(self, engine, position):
        # Price dropped exactly 10%: 700 -> 630
        signal = engine.evaluate_position(position, 630.0, date(2025, 2, 11))
        assert signal is not None
        assert signal.signal_type == "stop_loss"


class TestTakeProfit:
    def test_triggers_at_threshold(self, engine, position):
        # Price up 20%: 700 -> 840
        signal = engine.evaluate_position(position, 840.0, date(2025, 2, 11))
        assert signal is not None
        assert signal.signal_type == "take_profit"
        assert signal.return_pct >= 15.0

    def test_does_not_trigger_below_threshold(self, engine, position):
        # Price up 10%: 700 -> 770
        signal = engine.evaluate_position(position, 770.0, date(2025, 2, 11))
        assert signal is None


class TestHoldPeriod:
    def test_triggers_at_max_days(self, engine, position):
        # Held for 5 days, price flat
        signal = engine.evaluate_position(position, 700.0, date(2025, 2, 15))
        assert signal is not None
        assert signal.signal_type == "hold_period"

    def test_does_not_trigger_before_max_days(self, engine, position):
        # Held for 3 days, price flat
        signal = engine.evaluate_position(position, 700.0, date(2025, 2, 13))
        assert signal is None


class TestEvaluatePositions:
    def test_multiple_positions(self, engine):
        positions = [
            Position(
                llm_name=LLMProvider.CLAUDE,
                ticker="ASML.AS",
                quantity=Decimal("0.05"),
                avg_buy_price=Decimal("700.00"),
                is_real=True,
                opened_at=date(2025, 2, 10),
            ),
            Position(
                llm_name=LLMProvider.CLAUDE_AGGRESSIVE,
                ticker="SAP.DE",
                quantity=Decimal("0.1"),
                avg_buy_price=Decimal("200.00"),
                is_real=False,
                opened_at=date(2025, 2, 10),
            ),
        ]
        prices = {"ASML.AS": 620.0, "SAP.DE": 200.0}
        signals = engine.evaluate_positions(positions, prices, date(2025, 2, 11))
        assert len(signals) == 1
        assert signals[0].ticker == "ASML.AS"
        assert signals[0].signal_type == "stop_loss"

    def test_no_signals_when_prices_normal(self, engine):
        positions = [
            Position(
                llm_name=LLMProvider.CLAUDE,
                ticker="ASML.AS",
                quantity=Decimal("0.05"),
                avg_buy_price=Decimal("700.00"),
                is_real=True,
                opened_at=date(2025, 2, 10),
            ),
        ]
        prices = {"ASML.AS": 710.0}
        signals = engine.evaluate_positions(positions, prices, date(2025, 2, 11))
        assert len(signals) == 0

    def test_empty_positions(self, engine):
        signals = engine.evaluate_positions([], {}, date(2025, 2, 11))
        assert signals == []

    def test_zero_price_skipped(self, engine):
        positions = [
            Position(
                llm_name=LLMProvider.CLAUDE,
                ticker="ASML.AS",
                quantity=Decimal("0.05"),
                avg_buy_price=Decimal("700.00"),
                is_real=True,
                opened_at=date(2025, 2, 10),
            ),
        ]
        signals = engine.evaluate_positions(positions, {"ASML.AS": 0.0}, date(2025, 2, 11))
        assert len(signals) == 0


class TestPriorityOrder:
    def test_stop_loss_takes_priority_over_hold_period(self, engine, position):
        # Both stop-loss and hold-period would trigger
        signal = engine.evaluate_position(position, 620.0, date(2025, 2, 15))
        assert signal is not None
        assert signal.signal_type == "stop_loss"

    def test_take_profit_takes_priority_over_hold_period(self, engine, position):
        # Both take-profit and hold-period would trigger
        signal = engine.evaluate_position(position, 840.0, date(2025, 2, 15))
        assert signal is not None
        assert signal.signal_type == "take_profit"

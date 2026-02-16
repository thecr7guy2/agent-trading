from datetime import date

import pytest

from src.backtesting.engine import SimulatedPortfolio


class TestSimulatedPortfolio:
    def test_buy_creates_position(self):
        portfolio = SimulatedPortfolio()
        portfolio.buy("ASML.AS", 5.0, 700.0, date(2025, 2, 10))

        assert "ASML.AS" in portfolio.positions
        pos = portfolio.positions["ASML.AS"]
        assert pos.quantity == pytest.approx(5.0 / 700.0)
        assert pos.avg_buy_price == 700.0
        assert portfolio.total_invested == 5.0

    def test_buy_averages_price(self):
        portfolio = SimulatedPortfolio()
        portfolio.buy("ASML.AS", 700.0, 700.0, date(2025, 2, 10))
        portfolio.buy("ASML.AS", 800.0, 800.0, date(2025, 2, 11))

        pos = portfolio.positions["ASML.AS"]
        assert pos.quantity == pytest.approx(2.0)
        assert pos.avg_buy_price == pytest.approx(750.0)

    def test_sell_removes_position(self):
        portfolio = SimulatedPortfolio()
        portfolio.buy("ASML.AS", 700.0, 700.0, date(2025, 2, 10))
        result = portfolio.sell("ASML.AS", 800.0, date(2025, 2, 11))

        assert "ASML.AS" not in portfolio.positions
        assert result is not None
        assert result["pnl"] == pytest.approx(100.0)
        assert portfolio.realized_pnl == pytest.approx(100.0)

    def test_sell_nonexistent_returns_none(self):
        portfolio = SimulatedPortfolio()
        result = portfolio.sell("FAKE.XX", 100.0, date(2025, 2, 11))
        assert result is None

    def test_portfolio_value(self):
        portfolio = SimulatedPortfolio()
        portfolio.buy("ASML.AS", 700.0, 700.0, date(2025, 2, 10))
        portfolio.buy("SAP.DE", 200.0, 200.0, date(2025, 2, 10))

        value = portfolio.portfolio_value({"ASML.AS": 750.0, "SAP.DE": 210.0})
        expected = 1.0 * 750.0 + 1.0 * 210.0
        assert value == pytest.approx(expected)

    def test_portfolio_value_uses_buy_price_as_fallback(self):
        portfolio = SimulatedPortfolio()
        portfolio.buy("ASML.AS", 700.0, 700.0, date(2025, 2, 10))

        value = portfolio.portfolio_value({})
        assert value == pytest.approx(700.0)

    def test_buy_with_zero_price_ignored(self):
        portfolio = SimulatedPortfolio()
        portfolio.buy("ASML.AS", 5.0, 0.0, date(2025, 2, 10))
        assert "ASML.AS" not in portfolio.positions

    def test_sell_loss_recorded(self):
        portfolio = SimulatedPortfolio()
        portfolio.buy("ASML.AS", 700.0, 700.0, date(2025, 2, 10))
        result = portfolio.sell("ASML.AS", 600.0, date(2025, 2, 11))

        assert result is not None
        assert result["pnl"] == pytest.approx(-100.0)
        assert portfolio.realized_pnl == pytest.approx(-100.0)

    def test_trades_tracked(self):
        portfolio = SimulatedPortfolio()
        portfolio.buy("ASML.AS", 700.0, 700.0, date(2025, 2, 10))
        portfolio.sell("ASML.AS", 800.0, date(2025, 2, 11), reason="take_profit")

        assert len(portfolio.trades) == 2
        assert portfolio.trades[0]["action"] == "buy"
        assert portfolio.trades[1]["action"] == "sell"
        assert portfolio.trades[1]["reason"] == "take_profit"

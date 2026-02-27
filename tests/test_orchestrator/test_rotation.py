from datetime import date

from src.orchestrator.rotation import is_trading_day


class TestRotation:
    def test_trading_day_check(self):
        assert is_trading_day(date(2026, 2, 16), "Europe/Berlin") is True
        assert is_trading_day(date(2026, 2, 15), "Europe/Berlin") is False

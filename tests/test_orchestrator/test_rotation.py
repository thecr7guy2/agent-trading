from datetime import date

from src.db.models import LLMProvider
from src.orchestrator.rotation import get_main_trader, get_virtual_trader, is_trading_day


class TestRotation:
    def test_main_trader_weekday_mapping(self):
        assert get_main_trader(date(2026, 2, 16)) == LLMProvider.CLAUDE  # Monday
        assert get_main_trader(date(2026, 2, 17)) == LLMProvider.MINIMAX  # Tuesday
        assert get_main_trader(date(2026, 2, 18)) == LLMProvider.CLAUDE  # Wednesday
        assert get_main_trader(date(2026, 2, 19)) == LLMProvider.MINIMAX  # Thursday
        assert get_main_trader(date(2026, 2, 20)) == LLMProvider.CLAUDE  # Friday

    def test_virtual_trader_is_opposite(self):
        assert get_virtual_trader(date(2026, 2, 16)) == LLMProvider.MINIMAX
        assert get_virtual_trader(date(2026, 2, 17)) == LLMProvider.CLAUDE

    def test_trading_day_check(self):
        assert is_trading_day(date(2026, 2, 16), "Europe/Berlin") is True
        assert is_trading_day(date(2026, 2, 15), "Europe/Berlin") is False

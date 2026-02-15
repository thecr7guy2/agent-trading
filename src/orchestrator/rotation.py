from datetime import date, datetime
from zoneinfo import ZoneInfo

from src.db.models import LLMProvider


def get_main_trader(run_date: date) -> LLMProvider:
    weekday = run_date.weekday()
    return LLMProvider.CLAUDE if weekday in {0, 2, 4} else LLMProvider.MINIMAX


def get_virtual_trader(run_date: date) -> LLMProvider:
    main = get_main_trader(run_date)
    return LLMProvider.MINIMAX if main == LLMProvider.CLAUDE else LLMProvider.CLAUDE


def is_trading_day(run_date: date | None = None, timezone: str = "Europe/Berlin") -> bool:
    if run_date is None:
        run_date = datetime.now(ZoneInfo(timezone)).date()
    return run_date.weekday() < 5

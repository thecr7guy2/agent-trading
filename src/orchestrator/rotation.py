from datetime import date, datetime
from zoneinfo import ZoneInfo

from src.models import LLMProvider


def get_main_trader(run_date: date) -> LLMProvider:
    """Returns the conservative (real-money) trader — always Claude."""
    return LLMProvider.CLAUDE


def get_virtual_trader(run_date: date) -> LLMProvider:
    """Returns the aggressive (practice) trader — always Claude Aggressive."""
    return LLMProvider.CLAUDE_AGGRESSIVE


def is_trading_day(run_date: date | None = None, timezone: str = "Europe/Berlin") -> bool:
    if run_date is None:
        run_date = datetime.now(ZoneInfo(timezone)).date()
    return run_date.weekday() < 5

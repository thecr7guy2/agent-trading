from datetime import date, datetime
from zoneinfo import ZoneInfo


def is_trading_day(run_date: date | None = None, timezone: str = "Europe/Berlin") -> bool:
    if run_date is None:
        run_date = datetime.now(ZoneInfo(timezone)).date()
    return run_date.weekday() < 5

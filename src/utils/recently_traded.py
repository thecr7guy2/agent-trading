import json
import logging
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def _load(path: str) -> dict[str, str]:
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(path: str, data: dict[str, str]) -> None:
    Path(path).write_text(json.dumps(data, indent=2))


def add(ticker: str, path: str = "recently_traded.json") -> None:
    data = _load(path)
    data[ticker.upper()] = date.today().isoformat()
    _save(path, data)


def add_many(tickers: list[str], path: str = "recently_traded.json") -> None:
    data = _load(path)
    today = date.today().isoformat()
    for ticker in tickers:
        data[ticker.upper()] = today
    _save(path, data)


def get_blacklist(path: str = "recently_traded.json", days: int = 3) -> set[str]:
    data = _load(path)
    cutoff = date.today() - timedelta(days=days)
    return {t for t, d in data.items() if date.fromisoformat(d) >= cutoff}


def cleanup(path: str = "recently_traded.json", days: int = 3) -> None:
    data = _load(path)
    cutoff = date.today() - timedelta(days=days)
    fresh = {t: d for t, d in data.items() if date.fromisoformat(d) >= cutoff}
    _save(path, fresh)
    removed = len(data) - len(fresh)
    if removed:
        logger.info("Blacklist cleanup: removed %d expired tickers", removed)

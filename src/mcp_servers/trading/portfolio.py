"""
Thin wrapper around the T212 client for position and account queries.
No DB — T212 is the single source of truth for all positions and balances.
"""

import logging

from src.mcp_servers.trading.t212_client import T212Client

logger = logging.getLogger(__name__)


async def get_live_positions(t212: T212Client) -> list[dict]:
    """Return all open positions from the live (real money) account."""
    try:
        raw = await t212.get_positions()
        return _normalise_positions(raw)
    except Exception:
        logger.exception("Failed to fetch live positions")
        return []


async def get_demo_positions(t212: T212Client) -> list[dict]:
    """Return all open positions from the demo (practice) account."""
    try:
        raw = await t212.get_positions()
        return _normalise_positions(raw)
    except Exception:
        logger.exception("Failed to fetch demo positions")
        return []


async def get_account_cash(t212: T212Client) -> dict:
    """Return free cash and total account value."""
    try:
        data = await t212.get_account_cash()
        return {
            "free": float(data.get("free", data.get("freeForStocks", 0))),
            "invested": float(data.get("invested", 0)),
            "result": float(data.get("result", 0)),
            "total": float(data.get("total", 0)),
            "ppl": float(data.get("ppl", 0)),
        }
    except Exception:
        logger.exception("Failed to fetch account cash")
        return {"free": 0.0, "invested": 0.0, "result": 0.0, "total": 0.0, "ppl": 0.0}


def _normalise_positions(raw: list) -> list[dict]:
    """Normalise T212 position dicts into a consistent format."""
    positions = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        ticker = p.get("ticker", "")
        quantity = float(p.get("quantity", 0))
        avg_price = float(p.get("averagePrice", p.get("avgPrice", 0)))
        current_price = float(p.get("currentPrice", p.get("lastPrice", 0)))
        current_value = quantity * current_price
        invested = quantity * avg_price
        pnl = current_value - invested
        pnl_pct = (pnl / invested * 100) if invested else 0.0

        positions.append({
            "ticker": ticker,
            "quantity": quantity,
            "avg_buy_price": avg_price,
            "current_price": current_price,
            "current_value": round(current_value, 2),
            "pnl_eur": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "open_date": p.get("initialFillDate", p.get("openDate", "")),
        })
    return positions

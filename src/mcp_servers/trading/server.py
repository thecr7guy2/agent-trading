import logging
import sys

from mcp.server.fastmcp import FastMCP

from src.config import get_settings
from src.mcp_servers.trading.portfolio import (
    get_account_cash,
    get_demo_positions,
    get_live_positions,
)
from src.mcp_servers.trading.t212_client import T212Client, T212Error

# MCP stdio uses stdout for JSON-RPC — log to stderr only
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("trading")

_t212_live: T212Client | None = None
_t212_demo: T212Client | None = None


async def _get_t212_live() -> T212Client:
    global _t212_live
    if _t212_live is None:
        settings = get_settings()
        _t212_live = T212Client(
            api_key=settings.t212_api_key,
            api_secret=settings.t212_api_secret,
            use_demo=False,
        )
    return _t212_live


async def _get_t212_demo() -> T212Client | None:
    global _t212_demo
    if _t212_demo is None:
        settings = get_settings()
        if not settings.t212_practice_api_key:
            return None
        _t212_demo = T212Client(
            api_key=settings.t212_practice_api_key,
            api_secret=settings.t212_practice_api_secret or "",
            use_demo=True,
        )
    return _t212_demo


@mcp.tool()
async def place_buy_order(
    ticker: str,
    amount_eur: float,
    current_price: float,
    is_real: bool = True,
) -> dict:
    """Place a market buy order via Trading 212 for a specified EUR amount.
    Set is_real=True for the live account, is_real=False for the practice (demo) account.
    Calculates quantity from amount_eur / current_price and places a market order.
    Returns status 'filled' on success or 'error' with a reason on failure."""
    if amount_eur <= 0:
        return {"error": "amount_eur must be positive", "ticker": ticker}
    if current_price <= 0:
        return {"error": "current_price must be positive", "ticker": ticker}
    if not ticker or not ticker.strip():
        return {"error": "ticker must not be empty"}

    try:
        t212 = await _get_t212_live() if is_real else await _get_t212_demo()

        if t212 is None:
            return {
                "status": "error",
                "ticker": ticker,
                "error": "No practice T212 credentials configured",
            }

        # Pre-flight cash check
        try:
            cash = await t212.get_account_cash()
            free = float(cash.get("free", cash.get("freeForStocks", 0)))
            if free < amount_eur:
                return {
                    "status": "error",
                    "ticker": ticker,
                    "error": f"insufficient_funds (free: €{free:.2f}, requested: €{amount_eur:.2f})",
                }
        except Exception:
            pass  # let order attempt proceed if cash check fails

        broker_ticker = await t212.resolve_ticker(ticker)
        if not broker_ticker:
            return {
                "status": "error",
                "ticker": ticker,
                "error": "ticker not tradable on Trading 212",
            }

        quantity = amount_eur / current_price
        order = await t212.place_market_order(broker_ticker, quantity)

        filled_qty = float(order.get("filledQuantity", quantity))
        filled_value = float(order.get("filledValue", amount_eur))

        return {
            "status": "filled",
            "ticker": ticker,
            "broker_ticker": broker_ticker,
            "amount_eur": filled_value,
            "quantity": filled_qty,
            "is_real": is_real,
            "order_id": str(order.get("id", "")),
        }

    except T212Error as e:
        return {"status": "error", "ticker": ticker, "error": f"T212 {e.status_code}: {e.message}"}
    except Exception as e:
        logger.exception("place_buy_order failed for %s", ticker)
        return {"status": "error", "ticker": ticker, "error": str(e)}


@mcp.tool()
async def place_sell_order(
    ticker: str,
    quantity: float,
    is_real: bool = True,
) -> dict:
    """Place a market sell order via Trading 212 for a specified share quantity.
    Set is_real=True for the live account, is_real=False for the practice (demo) account."""
    if quantity <= 0:
        return {"error": "quantity must be positive", "ticker": ticker}
    if not ticker or not ticker.strip():
        return {"error": "ticker must not be empty"}

    try:
        t212 = await _get_t212_live() if is_real else await _get_t212_demo()
        if t212 is None:
            return {"status": "error", "ticker": ticker, "error": "No practice T212 credentials configured"}

        broker_ticker = await t212.resolve_ticker(ticker)
        if not broker_ticker:
            return {"status": "error", "ticker": ticker, "error": "ticker not tradable on Trading 212"}

        order = await t212.place_market_order(broker_ticker, -abs(quantity))
        filled_qty = float(abs(order.get("filledQuantity", quantity)))
        filled_value = float(order.get("filledValue", 0))

        return {
            "status": "filled",
            "ticker": ticker,
            "broker_ticker": broker_ticker,
            "quantity": filled_qty,
            "proceeds_eur": filled_value,
            "is_real": is_real,
            "order_id": str(order.get("id", "")),
        }

    except T212Error as e:
        return {"status": "error", "ticker": ticker, "error": f"T212 {e.status_code}: {e.message}"}
    except Exception as e:
        logger.exception("place_sell_order failed for %s", ticker)
        return {"status": "error", "ticker": ticker, "error": str(e)}


@mcp.tool()
async def get_positions(is_real: bool = True) -> dict:
    """Get all current open positions from Trading 212.
    Set is_real=True for the live account, is_real=False for the practice (demo) account."""
    try:
        if is_real:
            t212 = await _get_t212_live()
            positions = await get_live_positions(t212)
        else:
            t212 = await _get_t212_demo()
            if t212 is None:
                return {"error": "No practice T212 credentials configured", "positions": []}
            positions = await get_demo_positions(t212)

        return {"is_real": is_real, "count": len(positions), "positions": positions}

    except T212Error as e:
        return {"error": f"T212 {e.status_code}: {e.message}", "positions": []}
    except Exception as e:
        logger.exception("get_positions failed")
        return {"error": str(e), "positions": []}


@mcp.tool()
async def get_cash(is_real: bool = True) -> dict:
    """Get available cash balance from Trading 212.
    Set is_real=True for the live account, is_real=False for the practice (demo) account."""
    try:
        if is_real:
            t212 = await _get_t212_live()
        else:
            t212 = await _get_t212_demo()
            if t212 is None:
                return {"error": "No practice T212 credentials configured"}
        return {**await get_account_cash(t212), "is_real": is_real}
    except T212Error as e:
        return {"error": f"T212 {e.status_code}: {e.message}"}
    except Exception as e:
        logger.exception("get_cash failed")
        return {"error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")

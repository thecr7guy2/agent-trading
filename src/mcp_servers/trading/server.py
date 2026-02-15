import logging
import sys
from datetime import date
from decimal import Decimal

from mcp.server.fastmcp import FastMCP

from src.config import get_settings
from src.db.connection import get_pool
from src.mcp_servers.trading.portfolio import PortfolioManager
from src.mcp_servers.trading.t212_client import T212Client, T212Error

# MCP stdio uses stdout for JSON-RPC — log to stderr only
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("trading")

_t212: T212Client | None = None
_portfolio: PortfolioManager | None = None


async def _get_t212() -> T212Client:
    global _t212
    if _t212 is None:
        settings = get_settings()
        _t212 = T212Client(api_key=settings.t212_api_key)
    return _t212


async def _get_portfolio() -> PortfolioManager:
    global _portfolio
    if _portfolio is None:
        pool = await get_pool()
        _portfolio = PortfolioManager(pool)
    return _portfolio


@mcp.tool()
async def place_buy_order(ticker: str, amount_eur: float) -> dict:
    """Place a real buy order via Trading 212 for a specified EUR amount.
    Uses value-based ordering so Trading 212 handles fractional share calculation."""
    try:
        t212 = await _get_t212()
        result = await t212.place_value_order(ticker, amount_eur)

        # Record in DB
        pm = await _get_portfolio()
        filled_qty = Decimal(str(result.get("filledQuantity", 0)))
        filled_value = Decimal(str(result.get("filledValue", amount_eur)))
        filled_price = filled_value / filled_qty if filled_qty else Decimal("0")
        await pm.record_trade(
            llm_name="real",
            ticker=ticker,
            action="buy",
            quantity=filled_qty,
            price_per_share=filled_price,
            is_real=True,
            broker_order_id=str(result.get("id", "")),
        )

        return {"status": "filled", "ticker": ticker, "amount_eur": amount_eur, "order": result}
    except T212Error as e:
        logger.exception("place_buy_order failed for %s", ticker)
        return {"error": e.message, "status_code": e.status_code, "ticker": ticker}
    except Exception as e:
        logger.exception("place_buy_order failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def place_sell_order(ticker: str, quantity: float) -> dict:
    """Place a real sell order via Trading 212 for a specified share quantity."""
    try:
        t212 = await _get_t212()
        result = await t212.place_market_order(ticker, -abs(quantity))

        pm = await _get_portfolio()
        filled_qty = Decimal(str(abs(result.get("filledQuantity", quantity))))
        filled_value = Decimal(str(result.get("filledValue", 0)))
        filled_price = filled_value / filled_qty if filled_qty else Decimal("0")
        await pm.record_trade(
            llm_name="real",
            ticker=ticker,
            action="sell",
            quantity=filled_qty,
            price_per_share=filled_price,
            is_real=True,
            broker_order_id=str(result.get("id", "")),
        )

        return {"status": "filled", "ticker": ticker, "quantity": quantity, "order": result}
    except T212Error as e:
        logger.exception("place_sell_order failed for %s", ticker)
        return {"error": e.message, "status_code": e.status_code, "ticker": ticker}
    except Exception as e:
        logger.exception("place_sell_order failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def get_positions() -> dict:
    """Get current real positions from Trading 212."""
    try:
        t212 = await _get_t212()
        positions = await t212.get_positions()
        return {"count": len(positions), "positions": positions}
    except T212Error as e:
        logger.exception("get_positions failed")
        return {"error": e.message, "status_code": e.status_code}
    except Exception as e:
        logger.exception("get_positions failed")
        return {"error": str(e)}


@mcp.tool()
async def record_virtual_trade(
    llm_name: str, ticker: str, action: str, quantity: float, price: float
) -> dict:
    """Record a virtual (simulated) trade in the database for tracking.
    Used for the non-main-trader LLM's picks."""
    try:
        pm = await _get_portfolio()
        return await pm.record_trade(
            llm_name=llm_name,
            ticker=ticker,
            action=action,
            quantity=Decimal(str(quantity)),
            price_per_share=Decimal(str(price)),
            is_real=False,
        )
    except Exception as e:
        logger.exception("record_virtual_trade failed")
        return {"error": str(e), "llm_name": llm_name, "ticker": ticker}


@mcp.tool()
async def get_portfolio(llm_name: str) -> dict:
    """Get the full portfolio (all current positions) for a specific LLM from the database."""
    try:
        pm = await _get_portfolio()
        positions = await pm.get_portfolio(llm_name)
        return {"llm_name": llm_name, "count": len(positions), "positions": positions}
    except Exception as e:
        logger.exception("get_portfolio failed for %s", llm_name)
        return {"error": str(e), "llm_name": llm_name}


@mcp.tool()
async def get_pnl_report(llm_name: str, start_date: str, end_date: str) -> dict:
    """Calculate profit & loss for an LLM over a date range.
    Dates in ISO format (YYYY-MM-DD). Returns realized P&L from completed trades."""
    try:
        pm = await _get_portfolio()
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        return await pm.calculate_pnl(llm_name, start, end)
    except ValueError as e:
        return {"error": f"Invalid date format: {e}"}
    except Exception as e:
        logger.exception("get_pnl_report failed for %s", llm_name)
        return {"error": str(e), "llm_name": llm_name}


@mcp.tool()
async def get_leaderboard() -> dict:
    """Compare performance of all LLMs side by side, ranked by realized P&L."""
    try:
        pm = await _get_portfolio()
        entries = await pm.get_leaderboard()
        return {"count": len(entries), "leaderboard": entries}
    except Exception as e:
        logger.exception("get_leaderboard failed")
        return {"error": str(e)}


@mcp.tool()
async def get_trade_history(llm_name: str, limit: int = 50) -> dict:
    """Get recent trade history for a specific LLM, ordered by most recent first."""
    try:
        pm = await _get_portfolio()
        trades = await pm.get_trade_history(llm_name, limit)
        return {"llm_name": llm_name, "count": len(trades), "trades": trades}
    except Exception as e:
        logger.exception("get_trade_history failed for %s", llm_name)
        return {"error": str(e), "llm_name": llm_name}


if __name__ == "__main__":
    mcp.run(transport="stdio")

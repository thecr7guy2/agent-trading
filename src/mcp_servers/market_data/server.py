import logging
import sys

from mcp.server.fastmcp import FastMCP

from src.mcp_servers.market_data.finance import (
    get_earnings_calendar_upcoming,
    get_technical_indicators_for_ticker,
    get_ticker_earnings,
    get_ticker_fundamentals,
    get_ticker_history,
    get_ticker_info,
    get_ticker_news,
    is_eu_market_open,
    search_eu_stocks_by_query,
)
from src.mcp_servers.market_data.screener import screen_all_eu

# MCP stdio uses stdout for JSON-RPC — log to stderr only
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("market-data")

PERIOD_MAP = {
    7: "5d",
    14: "5d",
    30: "1mo",
    60: "3mo",
    90: "3mo",
    180: "6mo",
    365: "1y",
}


def _days_to_period(days: int) -> str:
    for threshold, period in sorted(PERIOD_MAP.items()):
        if days <= threshold:
            return period
    return "1y"


@mcp.tool()
async def get_stock_price(ticker: str) -> dict:
    """Get current/recent price for a stock ticker. Supports EU exchanges
    (append .AS, .PA, .DE, .MI, .MC, .L to the ticker symbol)."""
    try:
        return await get_ticker_info(ticker)
    except Exception as e:
        logger.exception("get_stock_price failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def get_stock_history(ticker: str, days: int = 30) -> dict:
    """Get historical OHLCV (open/high/low/close/volume) data for a ticker over N days."""
    try:
        period = _days_to_period(days)
        data = await get_ticker_history(ticker, period=period)
        return {"ticker": ticker, "days": days, "period": period, "data": data}
    except Exception as e:
        logger.exception("get_stock_history failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def get_fundamentals(ticker: str) -> dict:
    """Get fundamental data for a stock: P/E ratio, market cap, EPS, dividend yield,
    sector, industry, and more."""
    try:
        return await get_ticker_fundamentals(ticker)
    except Exception as e:
        logger.exception("get_fundamentals failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def get_technical_indicators(ticker: str) -> dict:
    """Get technical indicators for a stock: RSI, MACD, Bollinger Bands,
    and moving averages (SMA/EMA for 10, 20, 50, 200 periods)."""
    try:
        return await get_technical_indicators_for_ticker(ticker)
    except Exception as e:
        logger.exception("get_technical_indicators failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def search_eu_stocks(query: str) -> dict:
    """Search for EU-listed stocks by company name or partial ticker symbol.
    Checks across Amsterdam, Paris, Frankfurt, Milan, Madrid, and London exchanges."""
    try:
        results = await search_eu_stocks_by_query(query)
        return {"query": query, "results": results}
    except Exception as e:
        logger.exception("search_eu_stocks failed for %s", query)
        return {"error": str(e), "query": query}


@mcp.tool()
async def get_market_status() -> dict:
    """Check if European stock markets are currently open or closed (based on CET timezone)."""
    return is_eu_market_open()


@mcp.tool()
async def screen_eu_markets(
    exchanges: str = "AMS,PAR,GER,MIL,MCE,LSE",
    min_market_cap: int = 1_000_000_000,
) -> dict:
    """Screen EU exchanges for day gainers, losers, and most active stocks.
    Returns deduplicated list of tickers sorted by number of screener hits."""
    try:
        results = await screen_all_eu(exchanges, min_market_cap)
        return {"results": results, "count": len(results)}
    except Exception as e:
        logger.exception("screen_eu_markets failed")
        return {"error": str(e), "results": []}


@mcp.tool()
async def get_news(ticker: str, max_items: int = 5) -> dict:
    """Get recent news headlines for a stock ticker."""
    try:
        items = await get_ticker_news(ticker, max_items)
        return {"ticker": ticker, "news": items}
    except Exception as e:
        logger.exception("get_news failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def get_earnings_calendar() -> dict:
    """Get upcoming earnings calendar for the current week."""
    try:
        events = await get_earnings_calendar_upcoming()
        return {"events": events, "count": len(events)}
    except Exception as e:
        logger.exception("get_earnings_calendar failed")
        return {"error": str(e), "events": []}


@mcp.tool()
async def get_earnings(ticker: str) -> dict:
    """Get upcoming earnings date and estimates for a specific ticker."""
    try:
        return await get_ticker_earnings(ticker)
    except Exception as e:
        logger.exception("get_earnings failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def get_insider_buys(lookback_days: int = 7) -> dict:
    """Get recent CEO/director BUY transactions from BAFIN (German financial regulator).
    Returns insider purchases filed under MAR Article 19 for the past N days."""
    from src.mcp_servers.market_data.bafin import fetch_insider_buys

    try:
        trades = await fetch_insider_buys(lookback_days)
        return {"trades": trades, "count": len(trades)}
    except Exception as e:
        logger.exception("get_insider_buys failed")
        return {"error": str(e), "trades": [], "count": 0}


if __name__ == "__main__":
    mcp.run(transport="stdio")

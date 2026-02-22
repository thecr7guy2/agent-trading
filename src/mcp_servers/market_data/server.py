import logging
import sys

from mcp.server.fastmcp import FastMCP

from src.config import get_settings
from src.mcp_servers.market_data.earnings import get_earnings_revisions
from src.mcp_servers.market_data.finance import (
    get_earnings_calendar_upcoming,
    get_price_return_pct,
    get_technical_indicators_for_ticker,
    get_ticker_earnings,
    get_ticker_fundamentals,
    get_ticker_history,
    get_ticker_info,
    get_ticker_news,
    is_eu_market_open,
    search_eu_stocks_by_query,
)
from src.mcp_servers.market_data.insider import (
    get_insider_candidates,
    get_recent_insider_buys,
    get_ticker_insider_history,
)
from src.mcp_servers.market_data.news import get_company_news
from src.mcp_servers.market_data.screener import screen_markets

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
    """Get current price and basic quote data for a stock ticker."""
    try:
        return await get_ticker_info(ticker)
    except Exception as e:
        logger.exception("get_stock_price failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def get_stock_history(ticker: str, days: int = 30) -> dict:
    """Get historical OHLCV data for a ticker over the last N days."""
    try:
        period = _days_to_period(days)
        data = await get_ticker_history(ticker, period=period)
        return {"ticker": ticker, "days": days, "period": period, "data": data}
    except Exception as e:
        logger.exception("get_stock_history failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def get_fundamentals(ticker: str) -> dict:
    """Get fundamental data for a stock: P/E, market cap, EPS, revenue,
    profit margin, debt/equity, return on equity, and more."""
    try:
        return await get_ticker_fundamentals(ticker)
    except Exception as e:
        logger.exception("get_fundamentals failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def get_technical_indicators(ticker: str) -> dict:
    """Get technical indicators: RSI, MACD, Bollinger Bands,
    and moving averages (SMA/EMA for 10, 20, 50, 200 periods)."""
    try:
        return await get_technical_indicators_for_ticker(ticker)
    except Exception as e:
        logger.exception("get_technical_indicators failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def search_stocks(query: str) -> dict:
    """Search for stocks by company name or partial ticker."""
    try:
        results = await search_eu_stocks_by_query(query)
        return {"query": query, "results": results}
    except Exception as e:
        logger.exception("search_stocks failed for %s", query)
        return {"error": str(e), "query": query}


@mcp.tool()
async def get_market_status() -> dict:
    """Check if European stock markets are currently open (based on CET timezone)."""
    return is_eu_market_open()


@mcp.tool()
async def screen_global_markets() -> dict:
    """Screen global markets for top movers and most active stocks.
    Useful as supplementary context — not the primary candidate source."""
    try:
        results = await screen_markets(per_query_count=10)
        return {"results": results, "count": len(results)}
    except Exception as e:
        logger.exception("screen_global_markets failed")
        return {"error": str(e), "results": []}


@mcp.tool()
async def get_news(ticker: str, company_name: str = "", max_items: int = 5) -> dict:
    """Get recent news headlines for a stock from NewsAPI or yfinance."""
    try:
        settings = get_settings()
        name = company_name or ticker

        if settings.news_api_key:
            items = await get_company_news(name, ticker, settings.news_api_key, max_items)
            if items:
                return {"ticker": ticker, "source": "newsapi", "news": items}

        items = await get_ticker_news(ticker, max_items)
        return {"ticker": ticker, "source": "yfinance", "news": items}

    except Exception as e:
        logger.exception("get_news failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def get_insider_activity(days: int = 3) -> dict:
    """Get top insider buy candidates from OpenInsider (open-market purchases only, above $50K).
    Returns candidates ranked by conviction score (ΔOwn × title_multiplier × recency).
    Includes both cluster buys (2+ insiders) and solo C-suite high-ΔOwn purchases."""
    try:
        settings = get_settings()
        candidates = await get_insider_candidates(
            days=days,
            top_n=settings.insider_top_n,
        )
        transactions = await get_recent_insider_buys(days=days)
        return {
            "lookback_days": days,
            "total_transactions": len(transactions),
            "candidates": candidates,
        }
    except Exception as e:
        logger.exception("get_insider_activity failed")
        return {"error": str(e), "candidates": []}


@mcp.tool()
async def get_ticker_insider_history_tool(ticker: str, days: int = 90) -> dict:
    """Get historical insider buying pattern for a specific ticker from OpenInsider.
    Returns buy counts over 30/60/90 days and whether buying is accelerating
    (more buys recently than the prior period — accumulation signal)."""
    try:
        return await get_ticker_insider_history(ticker, days=days)
    except Exception as e:
        logger.exception("get_ticker_insider_history failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def get_price_returns(ticker: str) -> dict:
    """Get price returns for a ticker over 1 month, 6 months, and 1 year.
    Returns decimal values (e.g. -0.18 = -18%). Useful for identifying dip-buy
    opportunities where insiders are accumulating during a price decline."""
    try:
        r1m, r6m, r1y = await __import__("asyncio").gather(
            get_price_return_pct(ticker, "1mo"),
            get_price_return_pct(ticker, "6mo"),
            get_price_return_pct(ticker, "1y"),
        )
        return {
            "ticker": ticker,
            "return_1m": r1m,
            "return_6m": r6m,
            "return_1y": r1y,
        }
    except Exception as e:
        logger.exception("get_price_returns failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def get_analyst_revisions(ticker: str) -> dict:
    """Get analyst estimate revision trend for a stock."""
    try:
        settings = get_settings()
        return await get_earnings_revisions(ticker, settings.fmp_api_key)
    except Exception as e:
        logger.exception("get_analyst_revisions failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def get_earnings_calendar() -> dict:
    """Get upcoming earnings announcements for the current week."""
    try:
        events = await get_earnings_calendar_upcoming()
        return {"events": events, "count": len(events)}
    except Exception as e:
        logger.exception("get_earnings_calendar failed")
        return {"error": str(e), "events": []}


@mcp.tool()
async def get_earnings(ticker: str) -> dict:
    """Get next earnings date and EPS estimates for a specific ticker."""
    try:
        return await get_ticker_earnings(ticker)
    except Exception as e:
        logger.exception("get_earnings failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


if __name__ == "__main__":
    mcp.run(transport="stdio")

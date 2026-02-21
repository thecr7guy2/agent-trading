import logging
import sys

from mcp.server.fastmcp import FastMCP

from src.config import get_settings
from src.mcp_servers.market_data.earnings import get_earnings_revisions
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
from src.mcp_servers.market_data.insider import get_insider_cluster_buys, get_recent_insider_buys
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
    """Get current price and basic quote data for a stock ticker.
    For EU stocks append the exchange suffix: .DE (Frankfurt), .PA (Paris),
    .AS (Amsterdam), .MI (Milan), .MC (Madrid), .L (London)."""
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
    """Search for stocks by company name or partial ticker. Returns matching
    tickers across major exchanges."""
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
    EU-listed stocks receive a soft scoring bonus for preference.
    Returns candidates ranked by score, deduplicated across all queries."""
    try:
        settings = get_settings()
        results = await screen_markets(
            eu_preference_bonus=settings.eu_preference_bonus,
            per_query_count=10,
        )
        return {"results": results, "count": len(results)}
    except Exception as e:
        logger.exception("screen_global_markets failed")
        return {"error": str(e), "results": []}


@mcp.tool()
async def get_news(ticker: str, company_name: str = "", max_items: int = 5) -> dict:
    """Get recent news headlines for a stock from NewsAPI.
    Provide company_name for better search accuracy (e.g. 'SAP SE' not just 'SAP').
    Falls back to yfinance news if NewsAPI key is not configured."""
    try:
        settings = get_settings()
        name = company_name or ticker

        # Try NewsAPI first
        if settings.news_api_key:
            items = await get_company_news(name, ticker, settings.news_api_key, max_items)
            if items:
                return {"ticker": ticker, "source": "newsapi", "news": items}

        # Fallback: yfinance news
        items = await get_ticker_news(ticker, max_items)
        return {"ticker": ticker, "source": "yfinance", "news": items}

    except Exception as e:
        logger.exception("get_news failed for %s", ticker)
        return {"error": str(e), "ticker": ticker}


@mcp.tool()
async def get_insider_activity(days: int = 7) -> dict:
    """Get recent insider purchase transactions from OpenInsider (free, public data).
    Only includes open-market purchases (not option exercises) above $50K.
    Use this to find stocks where company insiders are buying with their own money —
    one of the strongest forward-looking signals available."""
    try:
        transactions = await get_recent_insider_buys(days=days)
        clusters = await get_insider_cluster_buys(days=days)
        return {
            "lookback_days": days,
            "total_transactions": len(transactions),
            "cluster_buys": clusters,           # 2+ insiders buying same stock
            "all_transactions": transactions,
        }
    except Exception as e:
        logger.exception("get_insider_activity failed")
        return {"error": str(e), "cluster_buys": [], "all_transactions": []}


@mcp.tool()
async def get_analyst_revisions(ticker: str) -> dict:
    """Get analyst estimate revision trend for a stock.
    Shows whether analysts are upgrading/downgrading and if EPS estimates
    are being revised up or down. Upward revisions are a strong bullish signal
    (post-earnings announcement drift effect)."""
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

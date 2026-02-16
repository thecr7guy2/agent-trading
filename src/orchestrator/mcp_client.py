from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class MCPToolClient(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict: ...

    async def close(self) -> None: ...


class InProcessMCPClient:
    def __init__(self, tools: dict[str, Callable[..., Coroutine[Any, Any, dict]]]):
        self._tools = tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict:
        func = self._tools.get(name)
        if func is None:
            return {"error": f"Unknown tool: {name}"}
        return await func(**arguments)

    async def close(self) -> None:
        pass


def create_trading_client() -> InProcessMCPClient:
    from src.mcp_servers.trading.server import (
        get_leaderboard,
        get_pnl_report,
        get_portfolio,
        get_positions,
        get_trade_history,
        place_buy_order,
        place_sell_order,
        record_virtual_trade,
    )

    return InProcessMCPClient(
        {
            "place_buy_order": place_buy_order,
            "place_sell_order": place_sell_order,
            "get_positions": get_positions,
            "record_virtual_trade": record_virtual_trade,
            "get_portfolio": get_portfolio,
            "get_pnl_report": get_pnl_report,
            "get_leaderboard": get_leaderboard,
            "get_trade_history": get_trade_history,
        }
    )


def create_reddit_client() -> InProcessMCPClient:
    from src.mcp_servers.reddit.server import (
        collect_posts,
        get_collection_stats,
        get_daily_digest,
        reset_collection,
    )

    return InProcessMCPClient(
        {
            "collect_posts": collect_posts,
            "get_daily_digest": get_daily_digest,
            "get_collection_stats": get_collection_stats,
            "reset_collection": reset_collection,
        }
    )


def create_market_data_client() -> InProcessMCPClient:
    from src.mcp_servers.market_data.server import (
        get_earnings,
        get_earnings_calendar,
        get_fundamentals,
        get_market_status,
        get_news,
        get_stock_history,
        get_stock_price,
        get_technical_indicators,
        screen_eu_markets,
        search_eu_stocks,
    )

    return InProcessMCPClient(
        {
            "get_stock_price": get_stock_price,
            "get_stock_history": get_stock_history,
            "get_fundamentals": get_fundamentals,
            "get_technical_indicators": get_technical_indicators,
            "search_eu_stocks": search_eu_stocks,
            "get_market_status": get_market_status,
            "screen_eu_markets": screen_eu_markets,
            "get_news": get_news,
            "get_earnings_calendar": get_earnings_calendar,
            "get_earnings": get_earnings,
        }
    )


class StdioMCPClient:
    """Skeleton for future use — connects to an MCP server over stdio transport."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict:
        raise NotImplementedError("StdioMCPClient is not yet wired up")

    async def close(self) -> None:
        pass

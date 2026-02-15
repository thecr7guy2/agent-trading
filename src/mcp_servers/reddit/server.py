import logging
import sys

from mcp.server.fastmcp import FastMCP

from src.config import get_settings
from src.mcp_servers.reddit.scraper import RSSCollector

# MCP stdio uses stdout for JSON-RPC — log to stderr only
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("reddit")

_collector: RSSCollector | None = None


def _get_collector() -> RSSCollector:
    global _collector
    if _collector is None:
        settings = get_settings()
        _collector = RSSCollector(user_agent=settings.reddit_user_agent)
    return _collector


@mcp.tool()
async def collect_posts(subreddits: list[str] | None = None) -> dict:
    """Trigger an RSS collection round — fetches hot/new/top feeds from all
    subreddits and accumulates posts. Call multiple times throughout the day
    to build up coverage before generating the digest."""
    try:
        collector = _get_collector()
        return await collector.collect(subreddits)
    except Exception as e:
        logger.exception("collect_posts failed")
        return {"error": str(e)}


@mcp.tool()
async def get_daily_digest(subreddits: list[str] | None = None) -> dict:
    """Get an aggregated daily summary of all stock mentions and sentiment
    from accumulated Reddit posts. This is the primary data source for the
    sentiment analysis stage. Call collect_posts first to accumulate data."""
    try:
        collector = _get_collector()
        return collector.get_daily_digest(subreddits)
    except Exception as e:
        logger.exception("get_daily_digest failed")
        return {"error": str(e)}


@mcp.tool()
async def get_collection_stats() -> dict:
    """Get statistics about the current collection state — number of rounds
    completed, total posts accumulated, and per-subreddit breakdown."""
    try:
        collector = _get_collector()
        return collector.get_collection_stats()
    except Exception as e:
        logger.exception("get_collection_stats failed")
        return {"error": str(e)}


@mcp.tool()
async def reset_collection() -> dict:
    """Clear all accumulated posts and reset the collection state.
    Call at the start of a new trading day."""
    try:
        collector = _get_collector()
        collector.reset()
        return {"status": "ok", "message": "Collection state cleared"}
    except Exception as e:
        logger.exception("reset_collection failed")
        return {"error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")

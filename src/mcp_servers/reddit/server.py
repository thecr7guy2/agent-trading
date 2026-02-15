import logging
import sys

from mcp.server.fastmcp import FastMCP

from src.config import get_settings
from src.mcp_servers.reddit.scraper import RedditScraper

# MCP stdio uses stdout for JSON-RPC — log to stderr only
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("reddit")

_scraper: RedditScraper | None = None


def _get_scraper() -> RedditScraper:
    global _scraper
    if _scraper is None:
        settings = get_settings()
        _scraper = RedditScraper(
            client_id=settings.reddit_client_id,
            client_secret=settings.reddit_client_secret,
            user_agent=settings.reddit_user_agent,
        )
    return _scraper


@mcp.tool()
async def search_subreddit(subreddit: str, query: str, limit: int = 25) -> dict:
    """Search a subreddit for posts matching keywords (stock tickers, company names).
    Returns matching posts with titles, scores, comment counts, and text previews."""
    try:
        scraper = _get_scraper()
        posts = await scraper.search_subreddit(subreddit, query, limit)
        return {"subreddit": subreddit, "query": query, "count": len(posts), "posts": posts}
    except Exception as e:
        logger.exception("search_subreddit failed for r/%s query=%s", subreddit, query)
        return {"error": str(e), "subreddit": subreddit, "query": query}


@mcp.tool()
async def get_trending_tickers(subreddits: list[str] | None = None, hours: int = 24) -> dict:
    """Get the most-mentioned stock tickers across Reddit investing subreddits
    in the last N hours. Returns tickers ranked by mention count with
    per-subreddit breakdown."""
    try:
        scraper = _get_scraper()
        tickers = await scraper.get_trending_tickers(subreddits, hours)
        return {"hours": hours, "count": len(tickers), "tickers": tickers}
    except Exception as e:
        logger.exception("get_trending_tickers failed")
        return {"error": str(e)}


@mcp.tool()
async def get_post_comments(post_id: str, limit: int = 50) -> dict:
    """Fetch top-level comments from a specific Reddit post for deeper
    sentiment analysis. Returns comment text, scores, and authors."""
    try:
        scraper = _get_scraper()
        comments = await scraper.get_post_comments(post_id, limit)
        return {"post_id": post_id, "count": len(comments), "comments": comments}
    except Exception as e:
        logger.exception("get_post_comments failed for post %s", post_id)
        return {"error": str(e), "post_id": post_id}


@mcp.tool()
async def get_daily_digest(subreddits: list[str] | None = None) -> dict:
    """Get an aggregated daily summary of all stock mentions and upvote-weighted
    sentiment from Reddit investing communities. This is the primary data source
    for the sentiment analysis stage."""
    try:
        scraper = _get_scraper()
        return await scraper.get_daily_digest(subreddits)
    except Exception as e:
        logger.exception("get_daily_digest failed")
        return {"error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")

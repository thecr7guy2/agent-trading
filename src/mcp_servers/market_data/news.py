import logging
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

NEWSAPI_URL = "https://newsapi.org/v2/everything"

# Circuit breaker: after a 429, skip all NewsAPI calls until this clears
_blocked_until: datetime | None = None


async def get_company_news(
    company_name: str,
    ticker: str,
    api_key: str,
    max_items: int = 5,
) -> list[dict]:
    """
    Fetch recent news headlines from NewsAPI for a given company.
    Returns empty list silently if api_key is not set or quota is exhausted.
    """
    global _blocked_until

    if not api_key:
        return []

    # Circuit breaker: quota exhausted — skip silently until reset
    if _blocked_until and datetime.now() < _blocked_until:
        return []

    query = f'"{company_name}"' if " " in company_name else company_name

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                NEWSAPI_URL,
                params={
                    "q": query,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": max_items,
                    "apiKey": api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        articles = data.get("articles", [])
        return [
            {
                "title": a.get("title", ""),
                "description": a.get("description", ""),
                "source": a.get("source", {}).get("name", ""),
                "published_at": a.get("publishedAt", ""),
                "url": a.get("url", ""),
            }
            for a in articles[:max_items]
            if a.get("title") and "[Removed]" not in a.get("title", "")
        ]

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            _blocked_until = datetime.now() + timedelta(hours=1)
            logger.warning("NewsAPI quota exhausted — falling back to yfinance news for this run")
        else:
            logger.warning("NewsAPI error for %s: %s", ticker, e.response.status_code)
        return []
    except Exception:
        logger.exception("NewsAPI failed for %s", ticker)
        return []

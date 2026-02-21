import logging

import httpx

logger = logging.getLogger(__name__)

NEWSAPI_URL = "https://newsapi.org/v2/everything"


async def get_company_news(
    company_name: str,
    ticker: str,
    api_key: str,
    max_items: int = 5,
) -> list[dict]:
    """
    Fetch recent news headlines from NewsAPI for a given company.
    Returns empty list silently if api_key is not set.
    """
    if not api_key:
        return []

    # Use company name as primary search term — more accurate than raw ticker
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
        logger.warning("NewsAPI error for %s: %s", ticker, e.response.status_code)
        return []
    except Exception:
        logger.exception("NewsAPI failed for %s", ticker)
        return []

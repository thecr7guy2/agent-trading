import asyncio
import logging

import yfinance as yf
from yfinance import EquityQuery

logger = logging.getLogger(__name__)

QUERY_CONFIGS = {
    "day_gainers": {"sortField": "percentchange", "sortAsc": False},
    "most_active": {"sortField": "dayvolume", "sortAsc": False},
}


async def _screen_global(query_type: str, count: int) -> list[dict]:
    config = QUERY_CONFIGS.get(query_type)
    if not config:
        return []

    def _fetch():
        try:
            # Global screen — no exchange restriction, min $500M market cap to exclude micro-caps
            query = EquityQuery("gt", ["intradaymarketcap", 500_000_000])
            result = yf.screen(
                query,
                sortField=config["sortField"],
                sortAsc=config["sortAsc"],
                size=count,
            )
            rows = []
            for quote in result.get("quotes", []):
                ticker = quote.get("symbol", "")
                if not ticker:
                    continue
                rows.append(
                    {
                        "ticker": ticker,
                        "name": quote.get("shortName") or quote.get("longName", ""),
                        "price": quote.get("regularMarketPrice"),
                        "change_pct": quote.get("regularMarketChangePercent"),
                        "volume": quote.get("regularMarketVolume"),
                        "market_cap": quote.get("marketCap"),
                        "source": f"global_{query_type}",
                    }
                )
            return rows
        except Exception:
            logger.exception("Global screener failed: query=%s", query_type)
            return []

    return await asyncio.to_thread(_fetch)


async def screen_markets(per_query_count: int = 10) -> list[dict]:
    """
    Screen global markets for top movers and most active stocks.
    Returns candidates ranked by hit count (appearances across multiple screens), deduplicated.
    """
    tasks = [_screen_global(query_type, per_query_count) for query_type in QUERY_CONFIGS]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    seen: dict[str, dict] = {}
    for result in results:
        if isinstance(result, Exception):
            logger.warning("Screen task failed: %s", result)
            continue
        for item in result:
            ticker = item["ticker"]
            if ticker not in seen:
                seen[ticker] = {**item, "hits": 1, "score": 1.0}
            else:
                seen[ticker]["hits"] += 1
                seen[ticker]["score"] = float(seen[ticker]["hits"])

    return sorted(seen.values(), key=lambda x: x["score"], reverse=True)

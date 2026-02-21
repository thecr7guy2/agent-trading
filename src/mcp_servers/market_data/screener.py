import asyncio
import logging

import yfinance as yf
from yfinance import EquityQuery

logger = logging.getLogger(__name__)

# EU ticker suffixes — used for soft preference bonus only, no hard filtering
EU_SUFFIXES = {".AS", ".PA", ".DE", ".MI", ".MC", ".L", ".BR", ".SW", ".ST", ".CO", ".OL", ".HE"}

# EU exchanges to screen explicitly so EU stocks get fair representation
EU_EXCHANGES = ["AMS", "PAR", "GER", "MIL", "MCE", "LSE"]

QUERY_CONFIGS = {
    "day_gainers": {"sortField": "percentchange", "sortAsc": False},
    "most_active": {"sortField": "dayvolume", "sortAsc": False},
}


def is_eu_ticker(ticker: str) -> bool:
    return any(ticker.endswith(s) for s in EU_SUFFIXES)


async def _screen_eu_exchange(exchange: str, query_type: str, count: int) -> list[dict]:
    config = QUERY_CONFIGS.get(query_type)
    if not config:
        return []

    def _fetch():
        try:
            query = EquityQuery("eq", ["exchange", exchange])
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
                rows.append({
                    "ticker": ticker,
                    "name": quote.get("shortName") or quote.get("longName", ""),
                    "price": quote.get("regularMarketPrice"),
                    "change_pct": quote.get("regularMarketChangePercent"),
                    "volume": quote.get("regularMarketVolume"),
                    "market_cap": quote.get("marketCap"),
                    "source": f"eu_{query_type}",
                    "is_eu": True,
                })
            return rows
        except Exception:
            logger.exception("EU screener failed: exchange=%s query=%s", exchange, query_type)
            return []

    return await asyncio.to_thread(_fetch)


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
                rows.append({
                    "ticker": ticker,
                    "name": quote.get("shortName") or quote.get("longName", ""),
                    "price": quote.get("regularMarketPrice"),
                    "change_pct": quote.get("regularMarketChangePercent"),
                    "volume": quote.get("regularMarketVolume"),
                    "market_cap": quote.get("marketCap"),
                    "source": f"global_{query_type}",
                    "is_eu": is_eu_ticker(ticker),
                })
            return rows
        except Exception:
            logger.exception("Global screener failed: query=%s", query_type)
            return []

    return await asyncio.to_thread(_fetch)


async def screen_markets(
    eu_preference_bonus: float = 0.1,
    per_query_count: int = 10,
) -> list[dict]:
    """
    Screen both EU exchanges and global markets.
    EU-listed stocks receive a soft scoring bonus — no hard exclusions.
    Returns candidates ranked by adjusted score, deduplicated.
    """
    tasks = []

    # EU exchange queries — ensures EU stocks get fair representation
    for exchange in EU_EXCHANGES:
        for query_type in QUERY_CONFIGS:
            tasks.append(_screen_eu_exchange(exchange, query_type, per_query_count))

    # Global queries — catches non-EU opportunities
    for query_type in QUERY_CONFIGS:
        tasks.append(_screen_global(query_type, per_query_count))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Deduplicate: track hit count per ticker
    seen: dict[str, dict] = {}
    for result in results:
        if isinstance(result, Exception):
            logger.warning("Screen task failed: %s", result)
            continue
        for item in result:
            ticker = item["ticker"]
            if ticker not in seen:
                seen[ticker] = {**item, "hits": 1}
            else:
                seen[ticker]["hits"] += 1
                # Keep is_eu=True if either pass flagged it
                if item.get("is_eu"):
                    seen[ticker]["is_eu"] = True

    # Apply EU soft bonus to score
    for entry in seen.values():
        base_score = float(entry["hits"])
        if entry.get("is_eu"):
            entry["score"] = base_score * (1.0 + eu_preference_bonus)
        else:
            entry["score"] = base_score

    return sorted(seen.values(), key=lambda x: x["score"], reverse=True)

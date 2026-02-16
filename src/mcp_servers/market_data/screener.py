import asyncio
import logging

import yfinance as yf
from yfinance import EquityQuery

logger = logging.getLogger(__name__)

VALID_EU_SUFFIXES = (".AS", ".PA", ".DE", ".MI", ".MC", ".L")

EXCHANGE_MAP = {
    "AMS": "AMS",
    "PAR": "PAR",
    "GER": "GER",
    "MIL": "MIL",
    "MCE": "MCE",
    "LSE": "LSE",
}

QUERY_CONFIGS = {
    "day_gainers": {"sortField": "percentchange", "sort_asc": False},
    "day_losers": {"sortField": "percentchange", "sort_asc": True},
    "most_active": {"sortField": "dayvolume", "sort_asc": False},
}


def _is_valid_eu_ticker(ticker: str) -> bool:
    return any(ticker.endswith(s) for s in VALID_EU_SUFFIXES)


async def screen_eu_exchange(
    exchange: str,
    query_type: str = "day_gainers",
    min_market_cap: int = 1_000_000_000,
    count: int = 10,
) -> list[dict]:
    config = QUERY_CONFIGS.get(query_type)
    if not config:
        return []

    def _fetch():
        try:
            query = EquityQuery(
                "and",
                [
                    EquityQuery("eq", ["exchange", exchange]),
                    EquityQuery("gt", ["intradaymarketcap", min_market_cap]),
                ],
            )
            result = yf.screen(
                query,
                sortField=config["sortField"],
                sortAsc=config["sort_asc"],
                size=count,
            )
            rows = []
            for quote in result.get("quotes", []):
                ticker = quote.get("symbol", "")
                if not ticker or not _is_valid_eu_ticker(ticker):
                    continue
                rows.append(
                    {
                        "ticker": ticker,
                        "name": quote.get("shortName") or quote.get("longName", ""),
                        "price": quote.get("regularMarketPrice"),
                        "change_pct": quote.get("regularMarketChangePercent"),
                        "volume": quote.get("regularMarketVolume"),
                        "market_cap": quote.get("marketCap"),
                        "exchange": exchange,
                        "query_type": query_type,
                    }
                )
            return rows
        except Exception:
            logger.exception("Screener failed for exchange=%s query=%s", exchange, query_type)
            return []

    return await asyncio.to_thread(_fetch)


async def screen_all_eu(
    exchanges: str = "AMS,PAR,GER,MIL,MCE,LSE",
    min_market_cap: int = 1_000_000_000,
    per_query_count: int = 10,
) -> list[dict]:
    exchange_list = [e.strip() for e in exchanges.split(",") if e.strip()]
    tasks = []
    for exchange in exchange_list:
        for query_type in QUERY_CONFIGS:
            tasks.append(screen_eu_exchange(exchange, query_type, min_market_cap, per_query_count))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    seen: dict[str, dict] = {}
    for result in results:
        if isinstance(result, Exception):
            logger.warning("Screen task failed: %s", result)
            continue
        for item in result:
            ticker = item["ticker"]
            if ticker not in seen:
                seen[ticker] = {
                    "ticker": ticker,
                    "name": item.get("name", ""),
                    "price": item.get("price"),
                    "change_pct": item.get("change_pct"),
                    "volume": item.get("volume"),
                    "market_cap": item.get("market_cap"),
                    "exchange": item.get("exchange"),
                    "screener_hits": [item.get("query_type", "")],
                }
            else:
                hit = item.get("query_type", "")
                if hit not in seen[ticker]["screener_hits"]:
                    seen[ticker]["screener_hits"].append(hit)

    return sorted(seen.values(), key=lambda x: len(x.get("screener_hits", [])), reverse=True)

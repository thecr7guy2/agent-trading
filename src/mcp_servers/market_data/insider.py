import asyncio
import logging
from collections import defaultdict

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

OPENINSIDER_URL = "https://openinsider.com/screener"

# Column indices in the OpenInsider screener table
_COL_FILING_DATE = 1
_COL_TRADE_DATE = 2
_COL_TICKER = 3
_COL_COMPANY = 4
_COL_INSIDER = 5
_COL_TITLE = 6
_COL_TRADE_TYPE = 7
_COL_PRICE = 8
_COL_QTY = 9
_COL_VALUE = 11


def _parse_value(text: str) -> float:
    """Convert '$1,234,567' or '$1.2M' style strings to a float."""
    clean = text.strip().replace("$", "").replace(",", "").replace("+", "")
    if not clean or clean == "-":
        return 0.0
    try:
        if clean.endswith("M"):
            return float(clean[:-1]) * 1_000_000
        if clean.endswith("K"):
            return float(clean[:-1]) * 1_000
        return float(clean)
    except ValueError:
        return 0.0


async def get_recent_insider_buys(days: int = 7, min_value: int = 50_000) -> list[dict]:
    """
    Scrape OpenInsider for recent insider purchase transactions.

    Returns a list of individual transactions. Cluster buys (multiple insiders
    buying the same stock) will appear as multiple entries for the same ticker —
    callers can group by ticker to identify cluster activity.

    Args:
        days: look-back window in days
        min_value: minimum transaction value in USD (default $50K)
    """

    def _fetch() -> list[dict]:
        try:
            params = {
                "fd": days,       # filing date range
                "xp": 1,          # include purchases
                "xs": 0,          # exclude sales
                "vl": min_value // 1000,  # min value in thousands
                "cnt": 40,        # max rows
                "action": 1,
                # exclude option exercises — we only want open-market buys
                "ocl": "",
                "och": "",
            }
            resp = httpx.get(
                OPENINSIDER_URL,
                params=params,
                headers={"User-Agent": "Mozilla/5.0 (compatible; trading-bot/1.0)"},
                timeout=15.0,
                follow_redirects=True,
            )
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "lxml")
            table = soup.find("table", {"class": "tinytable"})
            if not table:
                logger.warning("OpenInsider: no table found in response")
                return []

            rows = table.find_all("tr")[1:]  # skip header
            results = []
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 12:
                    continue

                ticker = cells[_COL_TICKER].get_text(strip=True)
                company = cells[_COL_COMPANY].get_text(strip=True)
                insider = cells[_COL_INSIDER].get_text(strip=True)
                title = cells[_COL_TITLE].get_text(strip=True)
                trade_type = cells[_COL_TRADE_TYPE].get_text(strip=True)
                trade_date = cells[_COL_TRADE_DATE].get_text(strip=True)
                price = _parse_value(cells[_COL_PRICE].get_text(strip=True))
                qty_raw = cells[_COL_QTY].get_text(strip=True).replace(",", "")
                value = _parse_value(cells[_COL_VALUE].get_text(strip=True))

                # Only open-market purchases (P) — skip option exercises (A)
                if trade_type not in ("P", "P+"):
                    continue
                if not ticker or not insider:
                    continue

                try:
                    qty = int(qty_raw.replace("+", "").replace("-", ""))
                except ValueError:
                    qty = 0

                results.append({
                    "ticker": ticker,
                    "company": company,
                    "insider_name": insider,
                    "title": title,
                    "trade_date": trade_date,
                    "price": price,
                    "shares": qty,
                    "value_usd": value,
                })

            return results

        except httpx.HTTPStatusError as e:
            logger.warning("OpenInsider HTTP error: %s", e.response.status_code)
            return []
        except Exception:
            logger.exception("OpenInsider scrape failed")
            return []

    return await asyncio.to_thread(_fetch)


async def get_insider_cluster_buys(days: int = 7, min_value: int = 50_000) -> list[dict]:
    """
    Returns tickers where 2+ distinct insiders have made purchases in the last N days.
    Sorted by total value bought descending — strongest conviction first.
    """
    transactions = await get_recent_insider_buys(days=days, min_value=min_value)

    grouped: dict[str, dict] = defaultdict(lambda: {
        "ticker": "",
        "company": "",
        "insiders": [],
        "total_value_usd": 0.0,
        "transactions": [],
    })

    for tx in transactions:
        ticker = tx["ticker"]
        g = grouped[ticker]
        g["ticker"] = ticker
        g["company"] = tx["company"]
        g["total_value_usd"] += tx["value_usd"]
        g["transactions"].append(tx)
        if tx["insider_name"] not in g["insiders"]:
            g["insiders"].append(tx["insider_name"])

    # Cluster = 2+ distinct insiders buying the same stock
    clusters = [
        {
            "ticker": g["ticker"],
            "company": g["company"],
            "insider_count": len(g["insiders"]),
            "insiders": g["insiders"],
            "total_value_usd": g["total_value_usd"],
            "transactions": g["transactions"],
        }
        for g in grouped.values()
        if len(g["insiders"]) >= 2
    ]

    return sorted(clusters, key=lambda x: x["total_value_usd"], reverse=True)

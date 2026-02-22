import asyncio
import logging
import math
from collections import defaultdict
from datetime import date, datetime

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

OPENINSIDER_URL = "http://openinsider.com/screener"

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
_COL_DELTA_OWN = 11
_COL_VALUE = 12

# C-suite titles get 3x weight — they have the most material context
_CSUITE_TITLES = {
    "ceo",
    "chief executive",
    "cfo",
    "chief financial",
    "coo",
    "chief operating",
    "president",
    "chairman",
    "chair",
    "cto",
    "chief technology",
    "executive chairman",
}


def _title_multiplier(title: str) -> float:
    lower = title.lower()
    if any(t in lower for t in _CSUITE_TITLES):
        return 3.0
    return 1.0


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


def _parse_delta_own(text: str) -> float:
    """Convert '+22%' or 'New' to a float percentage (22.0)."""
    clean = text.strip().replace("+", "").replace("%", "")
    if not clean or clean in ("-", "New", "new"):
        # 'New' means the insider had no prior stake — buying from scratch is maximum conviction
        return 100.0
    try:
        return float(clean)
    except ValueError:
        return 0.0


def _parse_trade_date(text: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _recency_decay(trade_date: date | None) -> float:
    """e^(-0.2 * days_since_trade) — fresh buys score higher."""
    if trade_date is None:
        return 0.5
    days = (date.today() - trade_date).days
    return math.exp(-0.2 * max(days, 0))


async def get_recent_insider_buys(days: int = 3, min_value: int = 25_000) -> list[dict]:
    """
    Scrape OpenInsider for recent insider purchase transactions.

    Returns a list of individual transactions including both cluster buys
    and solo high-conviction (CEO/CFO) purchases.
    Each transaction includes a conviction_score.
    """

    def _fetch() -> list[dict]:
        try:
            params = {
                "fd": days,
                "xp": 1,  # include purchases
                "xs": 0,  # exclude sales
                "vl": min_value // 1000,  # min value in thousands
                "cnt": 500,  # fetch 500 rows — cluster filter needs many raw transactions
                "action": 1,
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
                if len(cells) < 13:
                    continue

                ticker = cells[_COL_TICKER].get_text(strip=True)
                company = cells[_COL_COMPANY].get_text(strip=True)
                insider = cells[_COL_INSIDER].get_text(strip=True)
                title = cells[_COL_TITLE].get_text(strip=True)
                trade_type = cells[_COL_TRADE_TYPE].get_text(strip=True)
                trade_date_str = cells[_COL_TRADE_DATE].get_text(strip=True)
                price = _parse_value(cells[_COL_PRICE].get_text(strip=True))
                qty_raw = cells[_COL_QTY].get_text(strip=True).replace(",", "")
                delta_own_raw = cells[_COL_DELTA_OWN].get_text(strip=True)
                value = _parse_value(cells[_COL_VALUE].get_text(strip=True))

                # Only open-market purchases — skip option exercises, sales
                if not trade_type.startswith("P"):
                    continue
                if not ticker or not insider:
                    continue

                try:
                    qty = int(qty_raw.replace("+", "").replace("-", ""))
                except ValueError:
                    qty = 0

                trade_date = _parse_trade_date(trade_date_str)
                delta_own_pct = _parse_delta_own(delta_own_raw)
                title_mult = _title_multiplier(title)
                recency = _recency_decay(trade_date)
                conviction_score = delta_own_pct * title_mult * recency

                results.append(
                    {
                        "ticker": ticker,
                        "company": company,
                        "insider_name": insider,
                        "title": title,
                        "is_csuite": title_mult > 1.0,
                        "trade_date": trade_date_str,
                        "price": price,
                        "shares": qty,
                        "value_usd": value,
                        "delta_own_pct": delta_own_pct,
                        "conviction_score": round(conviction_score, 2),
                    }
                )

            return results

        except httpx.HTTPStatusError as e:
            logger.warning("OpenInsider HTTP error: %s", e.response.status_code)
            return []
        except Exception:
            logger.exception("OpenInsider scrape failed")
            return []

    return await asyncio.to_thread(_fetch)


async def get_insider_candidates(
    days: int = 3,
    min_value: int = 25_000,
    top_n: int = 25,
) -> list[dict]:
    """
    Returns the top N insider buy candidates ranked by conviction score.

    Includes both:
    - Cluster buys (2+ insiders buying the same stock) — grouped and scored
    - Solo high-conviction buys (C-suite with high ΔOwn) — included individually

    Each candidate has a unified conviction_score for ranking.
    """
    transactions = await get_recent_insider_buys(days=days, min_value=min_value)

    grouped: dict[str, dict] = defaultdict(
        lambda: {
            "ticker": "",
            "company": "",
            "insiders": [],
            "is_csuite_present": False,
            "total_value_usd": 0.0,
            "conviction_score": 0.0,
            "max_delta_own_pct": 0.0,
            "transactions": [],
        }
    )

    for tx in transactions:
        ticker = tx["ticker"]
        g = grouped[ticker]
        g["ticker"] = ticker
        g["company"] = tx["company"]
        g["total_value_usd"] += tx["value_usd"]
        g["conviction_score"] += tx["conviction_score"]
        g["max_delta_own_pct"] = max(g["max_delta_own_pct"], tx["delta_own_pct"])
        g["transactions"].append(tx)
        if tx["insider_name"] not in g["insiders"]:
            g["insiders"].append(tx["insider_name"])
        if tx["is_csuite"]:
            g["is_csuite_present"] = True

    candidates = []
    for g in grouped.values():
        if not g["ticker"]:
            continue
        insider_count = len(g["insiders"])
        is_cluster = insider_count >= 2
        # Solo C-suite with meaningful stake increase
        is_solo_csuite = (
            insider_count == 1 and g["is_csuite_present"] and g["max_delta_own_pct"] >= 3.0
        )
        # Solo buy of any title with large dollar conviction ($200K+)
        is_high_value_solo = insider_count == 1 and g["total_value_usd"] >= 200_000
        if not (is_cluster or is_solo_csuite or is_high_value_solo):
            continue

        candidates.append(
            {
                "ticker": g["ticker"],
                "company": g["company"],
                "insider_count": insider_count,
                "insiders": g["insiders"],
                "is_cluster": is_cluster,
                "is_csuite_present": g["is_csuite_present"],
                "total_value_usd": g["total_value_usd"],
                "conviction_score": round(g["conviction_score"], 2),
                "max_delta_own_pct": g["max_delta_own_pct"],
                "transactions": g["transactions"],
            }
        )

    return sorted(candidates, key=lambda x: x["conviction_score"], reverse=True)[:top_n]


async def get_ticker_insider_history(ticker: str, days: int = 90) -> dict:
    """
    Scrape openinsider.com/TICKER for the historical buy pattern of a specific stock.

    Returns buy counts in the last 30 / 60 / 90 days and whether buying is
    accelerating (more recent buys than older ones — accumulation pattern).
    """

    def _fetch() -> dict:
        try:
            resp = httpx.get(
                f"http://openinsider.com/{ticker}",
                headers={"User-Agent": "Mozilla/5.0 (compatible; trading-bot/1.0)"},
                timeout=15.0,
                follow_redirects=True,
            )
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "lxml")
            table = soup.find("table", {"class": "tinytable"})
            if not table:
                return {
                    "ticker": ticker,
                    "buys_30d": 0,
                    "buys_60d": 0,
                    "buys_90d": 0,
                    "accelerating": False,
                }

            today = date.today()
            buys_30d = buys_60d = buys_90d = 0

            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                if len(cells) < 8:
                    continue
                trade_type = cells[_COL_TRADE_TYPE].get_text(strip=True)
                if not trade_type.startswith("P"):
                    continue
                trade_date_str = cells[_COL_TRADE_DATE].get_text(strip=True)
                trade_date = _parse_trade_date(trade_date_str)
                if trade_date is None:
                    continue
                age_days = (today - trade_date).days
                if age_days <= 90:
                    buys_90d += 1
                if age_days <= 60:
                    buys_60d += 1
                if age_days <= 30:
                    buys_30d += 1

            # Accelerating = more buys in recent 30d than in the prior 30-60d window
            buys_30_60d = buys_60d - buys_30d
            accelerating = buys_30d > buys_30_60d and buys_30d > 0

            return {
                "ticker": ticker,
                "buys_30d": buys_30d,
                "buys_60d": buys_60d,
                "buys_90d": buys_90d,
                "accelerating": accelerating,
            }

        except httpx.HTTPStatusError as e:
            logger.warning(
                "OpenInsider history HTTP error for %s: %s", ticker, e.response.status_code
            )
            return {
                "ticker": ticker,
                "buys_30d": 0,
                "buys_60d": 0,
                "buys_90d": 0,
                "accelerating": False,
            }
        except Exception:
            logger.exception("OpenInsider history scrape failed for %s", ticker)
            return {
                "ticker": ticker,
                "buys_30d": 0,
                "buys_60d": 0,
                "buys_90d": 0,
                "accelerating": False,
            }

    return await asyncio.to_thread(_fetch)

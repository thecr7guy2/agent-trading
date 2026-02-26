import asyncio
import logging
import math
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.capitoltrades.com/trades"

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# K/M/B suffix multipliers
_SUFFIX = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def _parse_amount(text: str) -> tuple[float, float]:
    """Parse '50K–100K' or '1M–5M' into (lo, hi) as floats. Returns (0, 0) on failure."""
    text = text.strip()
    # en-dash or regular dash
    for sep in ("\u2013", "-"):
        if sep in text:
            parts = text.split(sep, 1)
            break
    else:
        return 0.0, 0.0

    def _parse_one(s: str) -> float:
        s = s.strip().replace("$", "").replace(",", "")
        for suffix, mult in _SUFFIX.items():
            if s.upper().endswith(suffix):
                try:
                    return float(s[:-1]) * mult
                except ValueError:
                    return 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0

    return _parse_one(parts[0]), _parse_one(parts[1])


def _parse_pub_date(cell) -> date | None:
    """
    Parse the publication date cell.
    Recent: inner divs = ['14:05', 'Today'] or ['14:05', 'Yesterday']
    Older:  inner divs = ['23 Feb', '2026']
    """
    inner = cell.find_all("div", class_=lambda c: c and "text-size" in c)
    if len(inner) < 2:
        return None
    part1 = inner[0].get_text(strip=True)  # time or "DD Mon"
    part2 = inner[1].get_text(strip=True)  # "Today"/"Yesterday" or year

    today = date.today()
    if part2 == "Today":
        return today
    if part2 == "Yesterday":
        return today - timedelta(days=1)
    # Older format: part1 = "23 Feb", part2 = "2026"
    try:
        return datetime.strptime(f"{part1} {part2}", "%d %b %Y").date()
    except ValueError:
        return None


def _parse_tx_date(cell) -> date | None:
    """Parse tx date cell — always 'DD Mon' + 'YYYY'."""
    inner = cell.find_all("div", class_=lambda c: c and "text-size" in c)
    if len(inner) < 2:
        return None
    try:
        return datetime.strptime(
            f"{inner[0].get_text(strip=True)} {inner[1].get_text(strip=True)}", "%d %b %Y"
        ).date()
    except ValueError:
        return None


def _recency_decay(trade_date: date | None) -> float:
    if trade_date is None:
        return 0.5
    days = (date.today() - trade_date).days
    return math.exp(-0.2 * max(days, 0))


def _scrape_page(session: cffi_requests.Session, page: int) -> list[dict]:
    try:
        resp = session.get(
            _BASE_URL,
            params={"per_page": 96, "page": page, "txType": "buy"},
            headers=_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Capitol Trades page %d fetch failed: %s", page, e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if not table:
        logger.warning("Capitol Trades page %d: no table found", page)
        return []

    tbody = table.find("tbody")
    if not tbody:
        return []

    rows = tbody.find_all("tr")
    results = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 8:
            continue

        # Ticker + company
        ticker_el = cells[1].find(class_="issuer-ticker")
        if not ticker_el:
            continue
        raw_ticker = ticker_el.get_text(strip=True)
        if not raw_ticker or raw_ticker == "N/A":
            continue
        # Strip exchange suffix e.g. "AMD:US" → "AMD"
        ticker = raw_ticker.split(":")[0].strip()
        if not ticker:
            continue

        issuer_el = cells[1].find(class_="issuer-name")
        company = issuer_el.get_text(strip=True) if issuer_el else ticker

        # Politician name
        pol_el = cells[0].find(class_="politician-name")
        politician = pol_el.get_text(strip=True) if pol_el else "Unknown"

        # Dates
        pub_date = _parse_pub_date(cells[2])
        tx_date = _parse_tx_date(cells[3])

        # Amount
        amount_el = cells[7].find(class_="trade-size")
        amount_text = amount_el.get_text(strip=True) if amount_el else ""
        lo, hi = _parse_amount(amount_text)
        midpoint = (lo + hi) / 2

        decay = _recency_decay(tx_date or pub_date)
        conviction = midpoint * decay

        results.append(
            {
                "ticker": ticker,
                "company": company,
                "politician_name": politician,
                "pub_date": pub_date.isoformat() if pub_date else "",
                "tx_date": tx_date.isoformat() if tx_date else "",
                "amount_text": amount_text,
                "amount_midpoint": midpoint,
                "conviction_score": round(conviction, 2),
                "_pub_date_obj": pub_date,
            }
        )
    return results


async def get_politician_candidates(
    lookback_days: int = 3,
    top_n: int = 10,
) -> list[dict]:
    """
    Scrape recent politician buy disclosures from Capitol Trades.

    Paginates until all rows on a page are older than lookback_days.
    Returns the top N candidates by conviction score, grouped by ticker.
    """

    def _fetch_all() -> list[dict]:
        session = cffi_requests.Session(impersonate="chrome136")
        cutoff = date.today() - timedelta(days=lookback_days)
        all_txs: list[dict] = []

        for page in range(1, 20):  # safety cap at 20 pages
            rows = _scrape_page(session, page)
            if not rows:
                break

            page_has_recent = False
            for row in rows:
                pd = row.pop("_pub_date_obj", None)
                if pd is not None and pd >= cutoff:
                    page_has_recent = True
                    all_txs.append(row)

            if not page_has_recent:
                break  # all remaining pages will be older

            time.sleep(0.5)  # polite rate limit

        logger.info("Capitol Trades: %d raw buy transactions scraped", len(all_txs))
        return all_txs

    raw = await asyncio.to_thread(_fetch_all)
    if not raw:
        return []

    grouped: dict[str, dict] = defaultdict(
        lambda: {
            "ticker": "",
            "company": "",
            "insiders": [],
            "total_value_usd": 0.0,
            "conviction_score": 0.0,
            "transactions": [],
        }
    )

    for tx in raw:
        ticker = tx["ticker"]
        g = grouped[ticker]
        g["ticker"] = ticker
        g["company"] = tx["company"]
        g["total_value_usd"] += tx["amount_midpoint"]
        g["conviction_score"] += tx["conviction_score"]
        g["transactions"].append(tx)
        if tx["politician_name"] not in g["insiders"]:
            g["insiders"].append(tx["politician_name"])

    candidates = []
    for g in grouped.values():
        if not g["ticker"]:
            continue
        insider_count = len(g["insiders"])
        candidates.append(
            {
                "ticker": g["ticker"],
                "company": g["company"],
                "source": "capitol_trades",
                "insider_count": insider_count,
                "insiders": g["insiders"],
                "is_cluster": insider_count >= 2,
                "is_csuite_present": False,
                "total_value_usd": g["total_value_usd"],
                "conviction_score": round(g["conviction_score"], 2),
                "max_delta_own_pct": 0.0,
                "transactions": g["transactions"],
            }
        )

    logger.info(
        "Capitol Trades: %d raw transactions → %d grouped candidates",
        len(raw),
        len(candidates),
    )
    return sorted(candidates, key=lambda x: x["conviction_score"], reverse=True)[:top_n]

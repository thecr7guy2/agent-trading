import asyncio
import logging
import math
from collections import defaultdict
from datetime import date, datetime

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

CAPITOL_TRADES_URL = "https://www.capitoltrades.com/trades"


def _parse_amount(text: str) -> float:
    """Parse amount strings like '5K', '1.5M' to float."""
    clean = text.strip().replace(",", "").replace("$", "")
    if not clean or clean == "-":
        return 0.0
    try:
        if clean.upper().endswith("M"):
            return float(clean[:-1]) * 1_000_000
        if clean.upper().endswith("K"):
            return float(clean[:-1]) * 1_000
        return float(clean)
    except ValueError:
        return 0.0


def _parse_trade_size(range_str: str) -> tuple[float, float]:
    """
    Convert Capitol Trades range strings to (min, max) USD amounts.

    Examples: "5K–15K" → (5000, 15000), "500K–1M" → (500000, 1000000)
    """
    # Handle en-dash (–) or regular hyphen (-)
    for sep in ["\u2013", "-"]:
        if sep in range_str:
            parts = range_str.split(sep, 1)
            if len(parts) == 2:
                lo = _parse_amount(parts[0].strip())
                hi = _parse_amount(parts[1].strip())
                if lo > 0 or hi > 0:
                    return lo, hi
    val = _parse_amount(range_str.strip())
    return val, val


def _parse_pub_date(text: str) -> date | None:
    """Parse publication date strings from Capitol Trades."""
    clean = text.strip()
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%m/%d/%Y", "%d %b %Y"):
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    return None


def _recency_decay(trade_date: date | None) -> float:
    """e^(-0.2 * days_since_trade) — fresh trades score higher."""
    if trade_date is None:
        return 0.5
    days = (date.today() - trade_date).days
    return math.exp(-0.2 * max(days, 0))


async def get_politician_candidates(
    lookback_days: int = 3,
    top_n: int = 10,
) -> list[dict]:
    """
    Scrape Capitol Trades for recent politician buy disclosures.

    Returns the top N candidates by conviction score (amount × recency decay),
    with the same candidate shape as OpenInsider so _enrich_candidate works unchanged.
    """

    def _fetch() -> list[dict]:
        try:
            resp = httpx.get(
                CAPITOL_TRADES_URL,
                headers={"User-Agent": "Mozilla/5.0 (compatible; trading-bot/1.0)"},
                timeout=20.0,
                follow_redirects=True,
            )
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.find_all("tr", class_="q-tr")
            if not rows:
                logger.warning("Capitol Trades: no trade rows found in response")
                return []

            cutoff = date.today()
            raw_transactions = []

            for row in rows:
                # Only process buy transactions
                if not row.find(class_="tx-type--buy"):
                    continue

                ticker_el = row.find("span", class_="q-field issuer-ticker")
                if not ticker_el:
                    continue
                ticker = ticker_el.get_text(strip=True)
                if not ticker:
                    continue

                company_el = row.find("h3", class_="q-field issuer-name")
                company = company_el.get_text(strip=True) if company_el else ticker

                # Politician name — try h3 first, then span
                pol_el = row.find("h3", class_="q-fieldset politician-name") or row.find(
                    "span", class_="q-fieldset politician-name"
                )
                politician = pol_el.get_text(strip=True) if pol_el else "Unknown"

                size_el = row.find("span", class_="q-field trade-size")
                range_str = size_el.get_text(strip=True) if size_el else ""

                date_el = row.find("div", class_="q-cell cell--pub-date")
                pub_date_str = date_el.get_text(strip=True) if date_el else ""
                pub_date = _parse_pub_date(pub_date_str)

                # Filter by lookback window
                if pub_date is not None:
                    days_old = (cutoff - pub_date).days
                    if days_old > lookback_days:
                        continue

                lo, hi = _parse_trade_size(range_str)
                midpoint = (lo + hi) / 2
                decay = _recency_decay(pub_date)
                conviction = midpoint * decay

                raw_transactions.append(
                    {
                        "ticker": ticker,
                        "company": company,
                        "politician_name": politician,
                        "pub_date": pub_date.isoformat() if pub_date else "",
                        "trade_size": range_str,
                        "amount_midpoint": midpoint,
                        "conviction_score": round(conviction, 2),
                    }
                )

            return raw_transactions

        except httpx.HTTPStatusError as e:
            logger.warning("Capitol Trades HTTP error: %s", e.response.status_code)
            return []
        except Exception:
            logger.exception("Capitol Trades scrape failed")
            return []

    raw = await asyncio.to_thread(_fetch)
    if not raw:
        return []

    # Group by ticker — multiple politicians buying same stock appear as separate rows
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

    return sorted(candidates, key=lambda x: x["conviction_score"], reverse=True)[:top_n]

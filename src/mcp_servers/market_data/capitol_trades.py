import asyncio
import logging
import math
from collections import defaultdict
from datetime import date, datetime

import httpx

logger = logging.getLogger(__name__)

# BFF API — returns clean JSON, no HTML parsing needed
BFF_URL = "https://bff.capitoltrades.com/trades"

# Browser headers to pass CloudFront checks on the BFF API
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.capitoltrades.com/trades",
    "Origin": "https://www.capitoltrades.com",
    "sec-ch-ua": '"Not/A)Brand";v="99", "Google Chrome";v="122", "Chromium";v="122"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}

# STOCK Act standard disclosure ranges (low_high format from BFF)
_RANGE_MAP: dict[str, tuple[float, float]] = {
    "1_999": (1, 999),
    "1001_15000": (1_001, 15_000),
    "15001_50000": (15_001, 50_000),
    "50001_100000": (50_001, 100_000),
    "100001_250000": (100_001, 250_000),
    "250001_500000": (250_001, 500_000),
    "500001_1000000": (500_001, 1_000_000),
    "1000001_5000000": (1_000_001, 5_000_000),
    "5000001_25000000": (5_000_001, 25_000_000),
    "25000001_50000000": (25_000_001, 50_000_000),
}


def _parse_value_range(value: str) -> tuple[float, float]:
    """
    Parse a Capitol Trades BFF value range string to (min, max) USD amounts.

    Handles:
      - Standard STOCK Act codes: "15001_50000" → (15001, 50000)
      - Generic underscore-separated: "100001_250000" → (100001, 250000)
    """
    if not value:
        return 0.0, 0.0
    if value in _RANGE_MAP:
        return _RANGE_MAP[value]
    # Generic fallback: try splitting on underscore
    parts = value.split("_")
    if len(parts) == 2:
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            pass
    return 0.0, 0.0


def _parse_date(text: str) -> date | None:
    """Parse ISO date strings from the BFF API."""
    if not text:
        return None
    # BFF returns dates as "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SSZ"
    clean = text[:10]
    try:
        return datetime.strptime(clean, "%Y-%m-%d").date()
    except ValueError:
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
    Fetch recent politician buy disclosures from the Capitol Trades BFF API.

    Returns the top N candidates by conviction score (amount × recency decay),
    with the same candidate shape as OpenInsider so _enrich_candidate works unchanged.
    """

    def _fetch() -> list[dict]:
        try:
            resp = httpx.get(
                BFF_URL,
                params={
                    "page": 0,
                    "pageSize": 96,
                    "sortBy": "-pubDate",
                    "txType": "buy",
                },
                headers=_HEADERS,
                timeout=20.0,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.warning(
                "Capitol Trades BFF HTTP %s — response: %s",
                e.response.status_code,
                e.response.text[:300],
            )
            return []
        except Exception:
            logger.exception("Capitol Trades BFF request failed")
            return []

        try:
            payload = resp.json()
        except Exception:
            logger.warning(
                "Capitol Trades BFF returned non-JSON. First 300 chars: %s", resp.text[:300]
            )
            return []

        # Log top-level keys on first call so we can diagnose unexpected shapes
        logger.debug("Capitol Trades BFF response keys: %s", list(payload.keys()))

        trades_raw = payload.get("data", [])
        if not isinstance(trades_raw, list):
            logger.warning(
                "Capitol Trades BFF: expected 'data' list, got %s. Keys: %s",
                type(trades_raw).__name__,
                list(payload.keys()),
            )
            return []

        if not trades_raw:
            logger.info("Capitol Trades BFF returned 0 trades (empty data array)")
            return []

        cutoff = date.today()
        raw_transactions = []

        for trade in trades_raw:
            # Pub date filter
            pub_date = _parse_date(trade.get("pubDate") or trade.get("filingDate", ""))
            if pub_date is not None:
                if (cutoff - pub_date).days > lookback_days:
                    continue

            # Only buy transactions (txType filter is in the request, but double-check)
            tx_type = (trade.get("txType") or trade.get("type", "")).lower()
            if tx_type not in ("buy", "purchase", "p"):
                continue

            # Ticker
            issuer = trade.get("issuer") or {}
            ticker = (
                issuer.get("localTicker") or issuer.get("ticker") or issuer.get("symbol") or ""
            ).strip()
            if not ticker:
                continue

            company = (issuer.get("name") or issuer.get("issuerName") or ticker).strip()

            # Politician
            pol = trade.get("politician") or trade.get("reporting") or {}
            first = pol.get("firstName") or pol.get("first_name") or ""
            last = pol.get("lastName") or pol.get("last_name") or ""
            politician_name = f"{first} {last}".strip() or "Unknown"

            # Amount / value range
            value_str = str(trade.get("value") or trade.get("amount") or "")
            lo, hi = _parse_value_range(value_str)
            midpoint = (lo + hi) / 2

            tx_date = _parse_date(trade.get("txDate") or trade.get("transactionDate", ""))
            decay = _recency_decay(tx_date or pub_date)
            conviction = midpoint * decay

            raw_transactions.append(
                {
                    "ticker": ticker,
                    "company": company,
                    "politician_name": politician_name,
                    "pub_date": pub_date.isoformat() if pub_date else "",
                    "tx_date": tx_date.isoformat() if tx_date else "",
                    "value_range": value_str,
                    "amount_midpoint": midpoint,
                    "conviction_score": round(conviction, 2),
                }
            )

        return raw_transactions

    raw = await asyncio.to_thread(_fetch)
    if not raw:
        return []

    # Group by ticker
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
        "Capitol Trades: %d raw buy transactions → %d grouped candidates",
        len(raw),
        len(candidates),
    )
    return sorted(candidates, key=lambda x: x["conviction_score"], reverse=True)[:top_n]

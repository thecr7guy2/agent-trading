import asyncio
import logging

import httpx
import yfinance as yf

logger = logging.getLogger(__name__)

FMP_BASE = "https://financialmodelingprep.com/api/v3"


async def get_earnings_revisions(ticker: str, fmp_api_key: str) -> dict:
    """
    Get analyst estimate revision trend for a ticker.

    Uses FMP if api_key is set, falls back to yfinance recommendation trend.
    Revision direction (up/down/flat) indicates whether analysts are growing
    more or less optimistic — upward revisions are a strong bullish signal.
    """
    if fmp_api_key:
        return await _fmp_revisions(ticker, fmp_api_key)
    return await _yfinance_fallback(ticker)


async def _fmp_revisions(ticker: str, api_key: str) -> dict:
    """
    Pulls two data points from FMP:
    1. Recent analyst upgrades/downgrades (last 30 days)
    2. Analyst EPS estimate trend (last 2 quarters)
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            # Upgrades / downgrades — most direct revision signal
            upgrades_resp = await client.get(
                f"{FMP_BASE}/upgrades-downgrades/{ticker}",
                params={"apikey": api_key, "limit": 10},
            )
            upgrades_resp.raise_for_status()
            upgrades = upgrades_resp.json()
        except Exception:
            logger.warning("FMP upgrades-downgrades failed for %s", ticker)
            upgrades = []

        try:
            # Analyst EPS consensus estimates — last 2 quarters
            estimates_resp = await client.get(
                f"{FMP_BASE}/analyst-estimates/{ticker}",
                params={"apikey": api_key, "period": "quarter", "limit": 2},
            )
            estimates_resp.raise_for_status()
            estimates = estimates_resp.json()
        except Exception:
            logger.warning("FMP analyst-estimates failed for %s", ticker)
            estimates = []

        try:
            # Current price target consensus
            target_resp = await client.get(
                f"{FMP_BASE}/price-target-consensus/{ticker}",
                params={"apikey": api_key},
            )
            target_resp.raise_for_status()
            target_data = target_resp.json()
            price_target = target_data[0] if target_data else {}
        except Exception:
            price_target = {}

    # Summarise upgrades/downgrades
    recent_upgrades = [
        u for u in upgrades if u.get("action") in ("upgrade", "initiated", "reiterated")
    ]
    recent_downgrades = [u for u in upgrades if u.get("action") == "downgrade"]

    # EPS revision direction
    eps_revision = "flat"
    if len(estimates) >= 2:
        newer_eps = estimates[0].get("estimatedEpsAvg")
        older_eps = estimates[1].get("estimatedEpsAvg")
        if newer_eps and older_eps and older_eps != 0:
            change_pct = (newer_eps - older_eps) / abs(older_eps) * 100
            if change_pct > 2:
                eps_revision = "up"
            elif change_pct < -2:
                eps_revision = "down"

    return {
        "ticker": ticker,
        "source": "fmp",
        "eps_revision_direction": eps_revision,
        "recent_upgrades": len(recent_upgrades),
        "recent_downgrades": len(recent_downgrades),
        "upgrade_details": [
            {
                "firm": u.get("gradingCompany"),
                "action": u.get("action"),
                "from_grade": u.get("previousGrade"),
                "to_grade": u.get("newGrade"),
                "date": u.get("date"),
            }
            for u in upgrades[:5]
        ],
        "price_target_consensus": {
            "target_high": price_target.get("targetHigh"),
            "target_low": price_target.get("targetLow"),
            "target_consensus": price_target.get("targetConsensus"),
            "target_median": price_target.get("targetMedian"),
        },
        "eps_estimates": [
            {
                "date": e.get("date"),
                "eps_avg": e.get("estimatedEpsAvg"),
                "revenue_avg": e.get("estimatedRevenueAvg"),
            }
            for e in estimates[:2]
        ],
    }


async def _yfinance_fallback(ticker: str) -> dict:
    """Fallback using yfinance recommendation trend when FMP key is unavailable."""

    def _fetch():
        try:
            t = yf.Ticker(ticker)
            recs = t.recommendations
            if recs is None or recs.empty:
                return {"ticker": ticker, "source": "yfinance", "error": "no data"}

            # Get last two periods to compute trend
            recent = recs.tail(2)
            result = []
            for _, row in recent.iterrows():
                result.append(
                    {
                        "period": str(row.name) if hasattr(row, "name") else "",
                        "strong_buy": int(row.get("strongBuy", 0)),
                        "buy": int(row.get("buy", 0)),
                        "hold": int(row.get("hold", 0)),
                        "sell": int(row.get("sell", 0)),
                        "strong_sell": int(row.get("strongSell", 0)),
                    }
                )

            # Simple revision signal: more buys in latest period vs previous
            revision = "flat"
            if len(result) == 2:
                latest_bulls = result[0]["strong_buy"] + result[0]["buy"]
                prev_bulls = result[1]["strong_buy"] + result[1]["buy"]
                if latest_bulls > prev_bulls:
                    revision = "up"
                elif latest_bulls < prev_bulls:
                    revision = "down"

            return {
                "ticker": ticker,
                "source": "yfinance",
                "eps_revision_direction": revision,
                "recommendation_trend": result,
            }
        except Exception:
            return {"ticker": ticker, "source": "yfinance", "error": "fetch failed"}

    return await asyncio.to_thread(_fetch)

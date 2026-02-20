import asyncio
import csv
import io
import logging
from datetime import date, timedelta

import httpx
import yfinance as yf
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_PORTAL_URL = "https://portal.mvp.bafin.de/database/DealingsInfo/sucheForm.do"

# CSV column positions (semicolon-delimited, latin-1 encoded)
# Columns vary by BAFIN export version — we detect by header row
_COL_DEFAULTS = {
    "isin": "ISIN",
    "company": "Emittent",
    "insider_name": "Name",
    "role": "Funktion",
    "transaction_type": "Art des Geschäfts",
    "transaction_date": "Datum",
    "price": "Preis",
    "volume": "Volumen",
    "total": "Gesamtvolumen",
}

_ISIN_CACHE: dict[str, str | None] = {}


async def fetch_insider_buys(lookback_days: int = 7) -> list[dict]:
    """
    Fetch CEO/director BUY transactions from BAFIN portal.
    Returns normalized dicts ready for signal_digest integration.
    """
    try:
        html = await _fetch_portal_html(lookback_days)
    except Exception:
        logger.exception("Failed to fetch BAFIN portal HTML")
        return []

    export_url = _extract_export_url(html)
    if not export_url:
        logger.warning("BAFIN: no export link found in portal response")
        return []

    try:
        csv_text = await _fetch_csv(export_url)
    except Exception:
        logger.exception("Failed to fetch BAFIN CSV from %s", export_url)
        return []

    rows = _parse_csv(csv_text)
    cutoff = date.today() - timedelta(days=lookback_days)
    buys = [r for r in rows if r.get("_buy") and r.get("_date") and r["_date"] >= cutoff]

    results = []
    for row in buys:
        isin = row.get("isin", "")
        if not isin:
            continue
        ticker = await _isin_to_ticker(isin)
        if not ticker:
            continue
        results.append(
            {
                "ticker": ticker,
                "isin": isin,
                "company_name": row.get("company", ""),
                "insider_name": row.get("insider_name", ""),
                "role": row.get("role", ""),
                "transaction_date": str(row["_date"]),
                "price": row.get("price", 0.0),
                "volume": row.get("volume", 0),
                "total_value": row.get("total", 0.0),
            }
        )

    logger.info("BAFIN: %d insider buy(s) resolved to tickers", len(results))
    return results


async def _fetch_portal_html(lookback_days: int) -> str:
    form_data = {
        "zeitraum": "2",  # "letzte Wochen" (recent period)
        "suchTyp": "1",
        "language": "de",
    }
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.post(_PORTAL_URL, data=form_data)
        resp.raise_for_status()
        return resp.text


def _extract_export_url(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    # BAFIN export links live in div.exportlinks or as direct CSV links
    for tag in soup.find_all("a", href=True):
        href: str = tag["href"]
        if "export" in href.lower() or ".csv" in href.lower() or "download" in href.lower():
            if href.startswith("http"):
                return href
            # relative URL — prepend base
            base = "https://portal.mvp.bafin.de"
            return base + href if href.startswith("/") else base + "/" + href
    return None


async def _fetch_csv(url: str) -> str:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        # BAFIN exports latin-1 encoded files
        return resp.content.decode("latin-1", errors="replace")


def _parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    rows = []
    for raw in reader:
        # Normalize column names (strip BOM, whitespace)
        row = {k.strip().lstrip("\ufeff"): v.strip() for k, v in raw.items() if k}

        transaction_type = _get_col(row, "Art des Geschäfts", "Transaktionsart", "Art")
        is_buy = transaction_type.strip().lower() in ("kauf", "buy", "erwerb")

        date_str = _get_col(row, "Datum", "Handelsdatum", "Meldedatum")
        tx_date = _parse_date(date_str)

        price_str = _get_col(row, "Preis", "Kurs")
        volume_str = _get_col(row, "Volumen", "Menge", "Stückzahl")
        total_str = _get_col(row, "Gesamtvolumen", "Gesamtbetrag", "Gesamtwert")

        rows.append(
            {
                "isin": _get_col(row, "ISIN"),
                "company": _get_col(row, "Emittent", "Gesellschaft", "Unternehmen"),
                "insider_name": _get_col(row, "Name", "Meldepflichtiger"),
                "role": _get_col(row, "Funktion", "Position", "Stellung"),
                "price": _parse_float(price_str),
                "volume": _parse_int(volume_str),
                "total": _parse_float(total_str),
                "_buy": is_buy,
                "_date": tx_date,
            }
        )
    return rows


def _get_col(row: dict, *keys: str) -> str:
    for key in keys:
        val = row.get(key, "")
        if val:
            return val
    return ""


def _parse_date(s: str) -> date | None:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return date.fromisoformat(s) if fmt == "%Y-%m-%d" else _strpdate(s, fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _strpdate(s: str, fmt: str) -> date:
    from datetime import datetime

    return datetime.strptime(s.strip(), fmt).date()


def _parse_float(s: str) -> float:
    try:
        return float(s.replace(".", "").replace(",", ".").strip())
    except (ValueError, AttributeError):
        return 0.0


def _parse_int(s: str) -> int:
    try:
        return int(s.replace(".", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0


async def _isin_to_ticker(isin: str) -> str | None:
    if isin in _ISIN_CACHE:
        return _ISIN_CACHE[isin]

    def _search() -> str | None:
        try:
            result = yf.Search(isin, max_results=3)
            quotes = result.quotes if hasattr(result, "quotes") else []
            if not quotes:
                return None
            # Prefer EU-listed tickers (have exchange suffix)
            eu_suffixes = (".AS", ".PA", ".DE", ".MI", ".MC", ".L")
            for q in quotes:
                sym = q.get("symbol", "")
                if any(sym.endswith(s) for s in eu_suffixes):
                    return sym
            # Fall back to first result
            return quotes[0].get("symbol")
        except Exception:
            logger.debug("yf.Search failed for ISIN %s", isin)
            return None

    ticker = await asyncio.to_thread(_search)
    _ISIN_CACHE[isin] = ticker
    return ticker

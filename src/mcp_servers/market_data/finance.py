import asyncio
from datetime import datetime, time
from zoneinfo import ZoneInfo

import yfinance as yf

EU_SUFFIXES: dict[str, str] = {
    "AS": "Euronext Amsterdam",
    "PA": "Euronext Paris",
    "DE": "Frankfurt (XETRA)",
    "MI": "Borsa Italiana",
    "MC": "Bolsa de Madrid",
    "L": "London Stock Exchange",
}

CET = ZoneInfo("Europe/Berlin")
MARKET_OPEN = time(9, 0)
MARKET_CLOSE = time(17, 30)


# --- yfinance async wrappers ---


async def get_ticker_info(ticker: str) -> dict:
    def _fetch():
        t = yf.Ticker(ticker)
        info = t.info
        return {
            "ticker": ticker,
            "name": info.get("shortName") or info.get("longName", ""),
            "price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "currency": info.get("currency", ""),
            "exchange": info.get("exchange", ""),
            "day_high": info.get("dayHigh"),
            "day_low": info.get("dayLow"),
            "day_change": info.get("regularMarketChange"),
            "day_change_pct": info.get("regularMarketChangePercent"),
            "volume": info.get("volume"),
            "previous_close": info.get("previousClose"),
        }

    return await asyncio.to_thread(_fetch)


async def get_ticker_history(ticker: str, period: str = "1mo") -> list[dict]:
    def _fetch():
        t = yf.Ticker(ticker)
        df = t.history(period=period)
        rows = []
        for idx, row in df.iterrows():
            rows.append(
                {
                    "date": idx.strftime("%Y-%m-%d"),
                    "open": round(float(row["Open"]), 4),
                    "high": round(float(row["High"]), 4),
                    "low": round(float(row["Low"]), 4),
                    "close": round(float(row["Close"]), 4),
                    "volume": int(row["Volume"]),
                }
            )
        return rows

    return await asyncio.to_thread(_fetch)


async def get_ticker_fundamentals(ticker: str) -> dict:
    def _fetch():
        t = yf.Ticker(ticker)
        info = t.info
        return {
            "ticker": ticker,
            "name": info.get("shortName") or info.get("longName", ""),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "eps": info.get("trailingEps"),
            "dividend_yield": info.get("dividendYield"),
            "price_to_book": info.get("priceToBook"),
            "revenue": info.get("totalRevenue"),
            "profit_margin": info.get("profitMargins"),
            "debt_to_equity": info.get("debtToEquity"),
            "return_on_equity": info.get("returnOnEquity"),
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
        }

    return await asyncio.to_thread(_fetch)


# --- Technical indicator calculations (pure math) ---


def compute_ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    multiplier = 2 / (period + 1)
    ema = [sum(values[:period]) / period]
    for price in values[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema


def compute_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_macd(closes: list[float]) -> dict | None:
    if len(closes) < 26:
        return None
    ema_12 = compute_ema(closes, 12)
    ema_26 = compute_ema(closes, 26)
    offset = len(ema_12) - len(ema_26)
    macd_line = [ema_12[offset + i] - ema_26[i] for i in range(len(ema_26))]

    if len(macd_line) < 9:
        return None
    signal_line = compute_ema(macd_line, 9)
    histogram = macd_line[-1] - signal_line[-1]

    return {
        "macd": round(macd_line[-1], 4),
        "signal": round(signal_line[-1], 4),
        "histogram": round(histogram, 4),
    }


def compute_bollinger_bands(closes: list[float], period: int = 20, num_std: int = 2) -> dict | None:
    if len(closes) < period:
        return None
    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std_dev = variance**0.5
    upper = middle + num_std * std_dev
    lower = middle - num_std * std_dev
    bandwidth = (upper - lower) / middle if middle != 0 else 0.0

    return {
        "upper": round(upper, 4),
        "middle": round(middle, 4),
        "lower": round(lower, 4),
        "bandwidth": round(bandwidth, 4),
    }


def compute_moving_averages(closes: list[float]) -> dict:
    result: dict[str, float | None] = {}
    for period in [10, 20, 50, 200]:
        if len(closes) >= period:
            result[f"sma_{period}"] = round(sum(closes[-period:]) / period, 4)
            ema_vals = compute_ema(closes, period)
            result[f"ema_{period}"] = round(ema_vals[-1], 4) if ema_vals else None
        else:
            result[f"sma_{period}"] = None
            result[f"ema_{period}"] = None
    return result


async def get_technical_indicators_for_ticker(ticker: str) -> dict:
    history = await get_ticker_history(ticker, period="6mo")
    if not history:
        return {"ticker": ticker, "error": "No historical data available"}

    closes = [row["close"] for row in history]
    return {
        "ticker": ticker,
        "data_points": len(closes),
        "current_price": closes[-1] if closes else None,
        "rsi": compute_rsi(closes),
        "macd": compute_macd(closes),
        "bollinger_bands": compute_bollinger_bands(closes),
        "moving_averages": compute_moving_averages(closes),
    }


# --- News + Earnings ---


async def get_ticker_news(ticker: str, max_items: int = 5) -> list[dict]:
    def _fetch():
        try:
            t = yf.Ticker(ticker)
            news = t.news
            items = []
            for entry in (news or [])[:max_items]:
                content = entry.get("content", {})
                items.append(
                    {
                        "title": content.get("title", entry.get("title", "")),
                        "summary": content.get("summary", ""),
                        "provider": content.get("provider", {}).get("displayName", ""),
                        "publish_date": content.get("pubDate", ""),
                    }
                )
            return items
        except Exception:
            return []

    return await asyncio.to_thread(_fetch)


async def get_earnings_calendar_upcoming() -> list[dict]:
    def _fetch():
        try:
            cal = yf.Calendars()
            df = cal.get_earnings_calendar()
            if df is None or df.empty:
                return []
            rows = []
            for _, row in df.iterrows():
                ticker = row.get("ticker", "")
                if not ticker:
                    continue
                rows.append(
                    {
                        "ticker": ticker,
                        "company": row.get("companyshortname", ""),
                        "event": row.get("startdatetype", ""),
                        "date": str(row.get("startdatetime", "")),
                        "eps_estimate": row.get("epsestimate"),
                    }
                )
            return rows
        except Exception:
            return []

    return await asyncio.to_thread(_fetch)


async def get_ticker_earnings(ticker: str) -> dict:
    def _fetch():
        try:
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal is None:
                return {"ticker": ticker, "earnings": None}
            if isinstance(cal, dict):
                return {"ticker": ticker, "earnings": cal}
            earnings = cal.to_dict() if hasattr(cal, "to_dict") else str(cal)
            return {"ticker": ticker, "earnings": earnings}
        except Exception:
            return {"ticker": ticker, "earnings": None}

    return await asyncio.to_thread(_fetch)


# --- EU stock search ---


async def search_eu_stocks_by_query(query: str) -> list[dict]:
    eu_suffix_set = {f".{s}" for s in EU_SUFFIXES}

    def _fetch():
        try:
            search = yf.Search(query, max_results=20)
            results = []
            for quote in search.quotes:
                symbol = quote.get("symbol", "")
                if not any(symbol.endswith(sfx) for sfx in eu_suffix_set):
                    continue
                suffix = symbol.rsplit(".", 1)[-1]
                results.append(
                    {
                        "ticker": symbol,
                        "name": quote.get("shortname") or quote.get("longname", ""),
                        "exchange": EU_SUFFIXES.get(suffix, quote.get("exchDisp", "")),
                        "sector": quote.get("sectorDisp", ""),
                        "industry": quote.get("industryDisp", ""),
                    }
                )
            return results
        except Exception:
            return []

    return await asyncio.to_thread(_fetch)


# --- Market status ---


def is_eu_market_open() -> dict:
    now = datetime.now(CET)
    weekday = now.weekday()  # 0=Mon, 6=Sun
    current_time = now.time()

    is_open = weekday < 5 and MARKET_OPEN <= current_time <= MARKET_CLOSE

    return {
        "is_open": is_open,
        "current_time_cet": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "weekday": now.strftime("%A"),
        "market_open": MARKET_OPEN.strftime("%H:%M"),
        "market_close": MARKET_CLOSE.strftime("%H:%M"),
    }

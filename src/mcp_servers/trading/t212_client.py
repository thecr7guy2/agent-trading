import asyncio
import base64
import logging
from decimal import ROUND_DOWN, Decimal

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class T212Error(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Trading 212 API error {status_code}: {message}")


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPError):
        return True
    if isinstance(exc, T212Error) and exc.status_code >= 500:
        return True
    return False


class T212Client:
    LIVE_BASE_URL = "https://live.trading212.com/api/v0"
    DEMO_BASE_URL = "https://demo.trading212.com/api/v0"
    MARKET_ORDER_QUANTITY_DECIMALS = 3
    SUFFIX_TO_COUNTRY = {
        "AS": "NL",
        "PA": "FR",
        "DE": "DE",
        "MI": "IT",
        "MC": "ES",
        "L": "GB",
    }
    # All T212 country codes to try when a specific suffix fails
    ALL_COUNTRIES = ("US", "NL", "FR", "DE", "IT", "ES", "GB")

    def __init__(self, api_key: str, api_secret: str, use_demo: bool = False):
        self._base_url = self.DEMO_BASE_URL if use_demo else self.LIVE_BASE_URL
        credentials = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        self._instruments_cache: list[dict] | None = None
        self._instruments_lock: asyncio.Lock = asyncio.Lock()
        self._resolved_ticker_cache: dict[str, str | None] = {}

    async def close(self):
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
        before_sleep=lambda rs: logger.warning(
            "T212 request retry #%d after %s", rs.attempt_number, rs.outcome.exception()
        ),
    )
    async def _request(self, method: str, path: str, **kwargs) -> dict | list:
        response = await self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise T212Error(response.status_code, response.text)
        if response.status_code == 204:
            return {}
        return response.json()

    async def get_instrument_quantity_precision(self, ticker: str) -> int:
        """Return the quantity precision (decimal places) required by T212 for a given ticker."""
        instruments = await self.get_instruments()
        for inst in instruments:
            if inst.get("ticker", "").upper() == ticker.upper():
                precision = inst.get("quantityPrecision")
                if precision is not None:
                    return int(precision)
        return self.MARKET_ORDER_QUANTITY_DECIMALS

    async def place_market_order(self, ticker: str, quantity: float) -> dict:
        """Place a market order. Positive quantity = buy, negative = sell.

        Tries from the instruments-list precision down to 0 (whole shares) because
        T212's quantityPrecision metadata is sometimes higher than what the order
        validation actually accepts (e.g. metadata says 2 but T212 requires 0).
        """
        # Always start at least at T212's standard 3dp — instruments metadata
        # sometimes returns a lower value (e.g. 2) which T212 order validation rejects.
        max_precision = max(
            await self.get_instrument_quantity_precision(ticker),
            self.MARKET_ORDER_QUANTITY_DECIMALS,
        )
        last_error: T212Error | None = None
        for precision in range(max_precision, -1, -1):
            step = Decimal("1").scaleb(-precision)
            normalized_quantity = float(
                Decimal(str(quantity)).quantize(step, rounding=ROUND_DOWN)
            )
            if normalized_quantity == 0.0:
                raise ValueError(
                    f"quantity rounds to 0 at {precision} decimal places; increase order size or use a lower price"
                )
            try:
                return await self._request(
                    "POST",
                    "/equity/orders/market",
                    json={"quantity": normalized_quantity, "ticker": ticker},
                )
            except T212Error as e:
                if "precision" in e.message.lower() and precision > 0:
                    logger.debug(
                        "Precision %d rejected for %s — retrying with %d", precision, ticker, precision - 1
                    )
                    last_error = e
                    continue
                raise
        raise last_error or T212Error(400, f"Could not place order for {ticker} at any precision")

    async def get_positions(self) -> list:
        return await self._request("GET", "/equity/portfolio")

    async def get_position(self, ticker: str) -> dict:
        return await self._request("GET", f"/equity/portfolio/{ticker}")

    async def get_account_cash(self) -> dict:
        return await self._request("GET", "/equity/account/cash")

    async def get_account_info(self) -> dict:
        return await self._request("GET", "/equity/account/info")

    async def get_pending_orders(self) -> list:
        return await self._request("GET", "/equity/orders")

    async def cancel_order(self, order_id: str) -> dict:
        return await self._request("DELETE", f"/equity/orders/{order_id}")

    async def get_instruments(self, force_refresh: bool = False) -> list:
        """Get list of all tradable instruments on Trading 212."""
        if self._instruments_cache is not None and not force_refresh:
            return self._instruments_cache
        async with self._instruments_lock:
            # Re-check after acquiring lock — another coroutine may have populated it
            if self._instruments_cache is not None and not force_refresh:
                return self._instruments_cache
            instruments = await self._request("GET", "/equity/metadata/instruments")
            self._instruments_cache = instruments if isinstance(instruments, list) else []
        return self._instruments_cache

    async def resolve_ticker(self, ticker: str) -> str | None:
        normalized = ticker.strip().upper()
        if not normalized:
            return None
        if normalized.endswith("_EQ"):
            return normalized
        if normalized in self._resolved_ticker_cache:
            return self._resolved_ticker_cache[normalized]

        instruments = await self.get_instruments()
        symbols: set[str] = set()
        for instrument in instruments:
            for key in ("ticker", "symbol", "instrumentCode", "code"):
                value = instrument.get(key)
                if isinstance(value, str) and value:
                    symbols.add(value.upper())

        # Step 1: Exact candidate matching (most reliable)
        candidates = self._build_candidates(normalized)
        for candidate in candidates:
            if candidate in symbols:
                logger.info("Ticker resolved: %s → %s (exact)", ticker, candidate)
                self._resolved_ticker_cache[normalized] = candidate
                return candidate

        base = normalized.split(".", maxsplit=1)[0] if "." in normalized else normalized

        # Step 2: For EU tickers, also try the base with ALL country codes.
        # Yahoo may use STMPA.PA (Paris) but T212 only lists STMPA_IT_EQ (Milan).
        if "." in normalized and len(base) >= 2:
            for country in self.ALL_COUNTRIES:
                candidate = f"{base}_{country}_EQ"
                if candidate in symbols:
                    logger.info("Ticker resolved: %s → %s (cross-exchange)", ticker, candidate)
                    self._resolved_ticker_cache[normalized] = candidate
                    return candidate

        # Step 3: Prefix fallback — T212 may use a shorter base symbol.
        # e.g. Yahoo: STMPA.PA, T212: STM_US_EQ  (STM is a prefix of STMPA)
        if len(base) >= 3:
            best_match = None
            best_len = 0
            for symbol in symbols:
                t212_base = symbol.split("_")[0]
                if len(t212_base) < 3:
                    continue
                if base.startswith(t212_base) and len(t212_base) > best_len:
                    best_match = symbol
                    best_len = len(t212_base)
            if best_match:
                logger.info("Ticker resolved: %s → %s (prefix fallback)", ticker, best_match)
                self._resolved_ticker_cache[normalized] = best_match
                return best_match

        # Step 4: Name-based search — T212 may use a completely different ticker
        # symbol but the instrument name contains the base.
        # e.g. Yahoo: ADYEN.AS, T212 ticker: 0YXG_GB_EQ, T212 name: "Adyen NV"
        if len(base) >= 4:
            for instrument in instruments:
                inst_name = (instrument.get("name") or "").upper()
                inst_short = (
                    instrument.get("shortName") or instrument.get("shortname") or ""
                ).upper()
                if base in inst_name or base in inst_short:
                    inst_ticker = instrument.get("ticker", "")
                    if inst_ticker:
                        logger.info(
                            "Ticker resolved: %s → %s (name match in '%s')",
                            ticker,
                            inst_ticker,
                            instrument.get("name", ""),
                        )
                        self._resolved_ticker_cache[normalized] = inst_ticker
                        return inst_ticker

        logger.warning(
            "Ticker resolution failed for %s — candidates tried: %s",
            ticker,
            candidates,
        )
        self._resolved_ticker_cache[normalized] = None
        return None

    def _build_candidates(self, ticker: str) -> list[str]:
        candidates: list[str] = []

        def _push(value: str):
            value = value.upper()
            if value and value not in candidates:
                candidates.append(value)

        _push(ticker)
        _push(ticker.replace(".", "_"))

        if "." in ticker:
            base, suffix = ticker.split(".", maxsplit=1)
            suffix = suffix.upper()
            _push(base)
            _push(f"{base}_EQ")
            _push(f"{base}_{suffix}")
            country = self.SUFFIX_TO_COUNTRY.get(suffix)
            if country:
                _push(f"{base}_{country}")
                _push(f"{base}_{country}_EQ")

        # Bare ticker (no dot or underscore) — try US formats
        if "." not in ticker and "_" not in ticker:
            _push(f"{ticker}_US_EQ")
            _push(f"{ticker}_US")

        if "_" in ticker and not ticker.endswith("_EQ"):
            _push(f"{ticker}_EQ")

        return candidates

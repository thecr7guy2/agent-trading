import base64
import logging

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
    SUFFIX_TO_COUNTRY = {
        "AS": "NL",
        "PA": "FR",
        "DE": "DE",
        "MI": "IT",
        "MC": "ES",
        "L": "GB",
    }

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

    async def place_market_order(self, ticker: str, quantity: float) -> dict:
        """Place a market order. Positive quantity = buy, negative = sell."""
        return await self._request(
            "POST",
            "/equity/orders/market",
            json={"quantity": quantity, "ticker": ticker},
        )

    async def place_value_order(self, ticker: str, value: float) -> dict:
        """Place a value-based order (buy a specific EUR/currency amount).
        Trading 212 calculates the fractional quantity automatically."""
        return await self._request(
            "POST",
            "/equity/orders/market",
            json={"value": value, "ticker": ticker},
        )

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
        if self._instruments_cache is None or force_refresh:
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

        for candidate in self._build_candidates(normalized):
            if candidate in symbols:
                self._resolved_ticker_cache[normalized] = candidate
                return candidate

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

import httpx


class T212Error(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Trading 212 API error {status_code}: {message}")


class T212Client:
    LIVE_BASE_URL = "https://live.trading212.com/api/v0"
    DEMO_BASE_URL = "https://demo.trading212.com/api/v0"

    def __init__(self, api_key: str, use_demo: bool = False):
        self._base_url = self.DEMO_BASE_URL if use_demo else self.LIVE_BASE_URL
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def close(self):
        await self._client.aclose()

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

    async def get_instruments(self) -> list:
        """Get list of all tradable instruments on Trading 212."""
        return await self._request("GET", "/equity/metadata/instruments")

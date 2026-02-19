from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.orchestrator.supervisor import Supervisor


def _settings():
    return SimpleNamespace(
        approval_timeout_seconds=120,
        approval_timeout_action="approve_all",
        market_data_ticker_limit=12,
        orchestrator_timezone="Europe/Berlin",
        daily_budget_eur=10.0,
        scheduler_eod_time="17:35",
        sell_stop_loss_pct=10.0,
        sell_take_profit_pct=15.0,
        sell_max_hold_days=5,
        sell_check_schedule="09:30,12:30,16:45",
        telegram_enabled=False,
        telegram_bot_token=None,
        telegram_chat_id=None,
        signal_candidate_limit=25,
        screener_min_market_cap=1_000_000_000,
        screener_exchanges="AMS,PAR,GER,MIL,MCE,LSE",
        max_tool_rounds=10,
        pipeline_timeout_seconds=600,
    )


class _MockMCPClient:
    def __init__(self, responses: dict | None = None):
        self._responses = responses or {}
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        resp = self._responses.get(name, {})
        if callable(resp):
            return resp(name, arguments)
        return resp

    async def close(self) -> None:
        pass


REDDIT_DIGEST = {
    "total_posts": 42,
    "tickers": [
        {
            "ticker": "ASML.AS",
            "mention_count": 10,
            "sentiment_score": 0.8,
            "top_quotes": ["great stock"],
            "subreddits": {"investing": 5},
        },
        {
            "ticker": "SAP.DE",
            "mention_count": 5,
            "sentiment_score": 0.3,
            "top_quotes": [],
            "subreddits": {"stocks": 3},
        },
    ],
}

SCREENER_RESULT = {
    "results": [
        {
            "ticker": "ASML.AS",
            "name": "ASML",
            "price": 850.0,
            "change_pct": 3.5,
            "volume": 1e6,
            "market_cap": 300e9,
            "exchange": "AMS",
            "screener_hits": ["day_gainers"],
        },
        {
            "ticker": "ING.AS",
            "name": "ING Group",
            "price": 15.0,
            "change_pct": 1.0,
            "volume": 500e3,
            "market_cap": 50e9,
            "exchange": "AMS",
            "screener_hits": ["most_active"],
        },
    ],
    "count": 2,
}

EARNINGS_RESULT = {
    "events": [
        {
            "ticker": "SAP.DE",
            "company": "SAP SE",
            "event": "Earnings",
            "date": "2026-02-20",
            "eps_estimate": 2.1,
        },
    ],
    "count": 1,
}


class TestBuildSignalDigest:
    @pytest.mark.asyncio
    async def test_merges_reddit_and_screener(self):
        news_result = {
            "ticker": "ASML.AS",
            "news": [{"title": "ASML beats estimates"}],
        }

        mock_reddit = _MockMCPClient({"get_daily_digest": REDDIT_DIGEST})
        mock_market = _MockMCPClient(
            {
                "screen_eu_markets": SCREENER_RESULT,
                "get_earnings_calendar": EARNINGS_RESULT,
                "get_news": news_result,
            }
        )

        supervisor = Supervisor(
            settings=_settings(),
            reddit_client=mock_reddit,
            market_data_client=mock_market,
        )

        digest = await supervisor.build_signal_digest()

        assert digest["source_type"] == "multi"
        assert digest["total_posts"] == 42
        candidates = digest["candidates"]

        tickers = [c["ticker"] for c in candidates]
        assert "ASML.AS" in tickers
        assert "SAP.DE" in tickers
        assert "ING.AS" in tickers

        asml = next(c for c in candidates if c["ticker"] == "ASML.AS")
        assert "reddit" in asml["sources"]
        assert "screener" in asml["sources"]

        sap = next(c for c in candidates if c["ticker"] == "SAP.DE")
        assert "reddit" in sap["sources"]
        assert "earnings" in sap["sources"]

    @pytest.mark.asyncio
    async def test_candidate_limit(self):
        settings = _settings()
        settings.signal_candidate_limit = 2

        reddit_digest = {
            "total_posts": 100,
            "tickers": [
                {
                    "ticker": f"T{i}.AS",
                    "mention_count": 10 - i,
                    "sentiment_score": 0.5,
                }
                for i in range(5)
            ],
        }

        mock_reddit = _MockMCPClient({"get_daily_digest": reddit_digest})
        mock_market = _MockMCPClient(
            {
                "screen_eu_markets": {"results": [], "count": 0},
                "get_earnings_calendar": {"events": [], "count": 0},
                "get_news": {"ticker": "", "news": []},
            }
        )

        supervisor = Supervisor(
            settings=settings,
            reddit_client=mock_reddit,
            market_data_client=mock_market,
        )

        digest = await supervisor.build_signal_digest()
        assert len(digest["candidates"]) == 2

    @pytest.mark.asyncio
    async def test_fallback_on_screener_failure(self):
        reddit_digest = {
            "total_posts": 10,
            "tickers": [
                {
                    "ticker": "ASML.AS",
                    "mention_count": 5,
                    "sentiment_score": 0.6,
                },
            ],
        }

        mock_reddit = _MockMCPClient({"get_daily_digest": reddit_digest})
        mock_market = _MockMCPClient(
            {
                "screen_eu_markets": {"results": [], "count": 0},
                "get_earnings_calendar": {"events": [], "count": 0},
                "get_news": {"ticker": "", "news": []},
            }
        )
        original_call = mock_market.call_tool

        async def patched_call(name, arguments):
            if name == "screen_eu_markets":
                raise Exception("screener down")
            return await original_call(name, arguments)

        mock_market.call_tool = patched_call

        supervisor = Supervisor(
            settings=_settings(),
            reddit_client=mock_reddit,
            market_data_client=mock_market,
        )

        digest = await supervisor.build_signal_digest()
        assert digest["source_type"] == "multi"
        tickers = [c["ticker"] for c in digest["candidates"]]
        assert "ASML.AS" in tickers

    @pytest.mark.asyncio
    async def test_news_enrichment(self):
        reddit_digest = {
            "total_posts": 10,
            "tickers": [
                {
                    "ticker": "ASML.AS",
                    "mention_count": 5,
                    "sentiment_score": 0.6,
                },
            ],
        }

        def mock_market_response(name, arguments):
            if name == "screen_eu_markets":
                return {"results": [], "count": 0}
            if name == "get_earnings_calendar":
                return {"events": [], "count": 0}
            if name == "get_news":
                return {
                    "ticker": arguments.get("ticker", ""),
                    "news": [{"title": "Test headline"}],
                }
            return {}

        mock_reddit = _MockMCPClient({"get_daily_digest": reddit_digest})
        mock_market = _MockMCPClient({})
        mock_market.call_tool = AsyncMock(side_effect=lambda n, a: mock_market_response(n, a))

        supervisor = Supervisor(
            settings=_settings(),
            reddit_client=mock_reddit,
            market_data_client=mock_market,
        )
        supervisor._ensure_clients = lambda: None

        digest = await supervisor.build_signal_digest()
        asml = next(c for c in digest["candidates"] if c["ticker"] == "ASML.AS")
        assert "news" in asml
        assert asml["news"][0]["title"] == "Test headline"


class TestBuildMarketDataNewFormat:
    @pytest.mark.asyncio
    async def test_uses_candidates_key(self):
        digest = {
            "candidates": [
                {"ticker": "ASML.AS", "sources": ["reddit", "screener"]},
                {"ticker": "SAP.DE", "sources": ["reddit"]},
            ],
            "source_type": "multi",
        }

        mock_market = _MockMCPClient(
            {
                "get_stock_price": {"price": 850.0},
                "get_fundamentals": {"ticker": "ASML.AS"},
                "get_technical_indicators": {"ticker": "ASML.AS"},
            }
        )

        supervisor = Supervisor(
            settings=_settings(),
            market_data_client=mock_market,
        )

        market_data = await supervisor.build_market_data(digest)
        assert "ASML.AS" in market_data
        assert "SAP.DE" in market_data

    @pytest.mark.asyncio
    async def test_backward_compat_tickers_key(self):
        digest = {
            "tickers": [
                {"ticker": "ASML.AS"},
            ],
        }

        mock_market = _MockMCPClient(
            {
                "get_stock_price": {"price": 850.0},
                "get_fundamentals": {"ticker": "ASML.AS"},
                "get_technical_indicators": {"ticker": "ASML.AS"},
            }
        )

        supervisor = Supervisor(
            settings=_settings(),
            market_data_client=mock_market,
        )

        market_data = await supervisor.build_market_data(digest)
        assert "ASML.AS" in market_data

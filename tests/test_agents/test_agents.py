import json
from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.agents.market_agent import MarketAgent
from src.agents.sentiment_agent import SentimentAgent
from src.agents.trader_agent import TraderAgent
from src.db.models import (
    AgentStage,
    DailyPicks,
    LLMProvider,
    MarketAnalysis,
    ResearchFinding,
    ResearchReport,
    SentimentReport,
    TickerAnalysis,
    TickerSentiment,
)

# --- Sample data fixtures ---

SAMPLE_REDDIT_DIGEST = {
    "date": "2026-02-15",
    "subreddits_scraped": ["wallstreetbets", "investing"],
    "total_posts": 100,
    "tickers": [
        {"ticker": "ASML.AS", "mentions": 15, "sentiment_score": 0.7, "top_posts": []},
        {"ticker": "SAP.DE", "mentions": 8, "sentiment_score": 0.3, "top_posts": []},
    ],
}

SAMPLE_SENTIMENT = SentimentReport(
    report_date=date(2026, 2, 15),
    tickers=[
        TickerSentiment(ticker="ASML.AS", mention_count=15, sentiment_score=0.7),
        TickerSentiment(ticker="SAP.DE", mention_count=8, sentiment_score=0.3),
    ],
    total_posts_analyzed=100,
    subreddits_scraped=["wallstreetbets", "investing"],
)

SAMPLE_MARKET_DATA = {
    "ASML.AS": {"price": 850.0, "pe_ratio": 35.0, "rsi": 55.0},
    "SAP.DE": {"price": 200.0, "pe_ratio": 28.0, "rsi": 62.0},
}

SAMPLE_MARKET_ANALYSIS = MarketAnalysis(
    analysis_date=date(2026, 2, 15),
    tickers=[
        TickerAnalysis(
            ticker="ASML.AS",
            exchange="Euronext Amsterdam",
            current_price=850,
            fundamental_score=8.5,
            technical_score=7.0,
            risk_score=3.5,
            summary="Strong fundamentals, bullish technicals.",
        ),
    ],
)

SENTIMENT_RESPONSE_JSON = json.dumps(
    {
        "report_date": "2026-02-15",
        "tickers": [
            {
                "ticker": "ASML.AS",
                "mention_count": 15,
                "sentiment_score": 0.72,
                "top_quotes": ["ASML is crushing it"],
                "subreddits": {"wallstreetbets": 10, "investing": 5},
            }
        ],
        "total_posts_analyzed": 100,
        "subreddits_scraped": ["wallstreetbets", "investing"],
    }
)

MARKET_RESPONSE_JSON = json.dumps(
    {
        "analysis_date": "2026-02-15",
        "tickers": [
            {
                "ticker": "ASML.AS",
                "exchange": "Euronext Amsterdam",
                "current_price": 850.5,
                "currency": "EUR",
                "fundamental_score": 8.5,
                "technical_score": 7.0,
                "risk_score": 3.5,
                "summary": "Strong fundamentals and bullish technicals.",
            }
        ],
    }
)

TRADER_RESPONSE_JSON = json.dumps(
    {
        "llm": "claude",
        "pick_date": "2026-02-15",
        "picks": [
            {
                "ticker": "ASML.AS",
                "exchange": "Euronext Amsterdam",
                "allocation_pct": 60.0,
                "reasoning": "Strong fundamentals and positive sentiment.",
                "action": "buy",
            }
        ],
        "sell_recommendations": [],
        "confidence": 0.75,
        "market_summary": "EU markets looking strong today.",
    }
)


# --- Helper to create a mock provider ---


def _mock_provider(response_json: str, output_model):
    provider = AsyncMock()
    provider.generate = AsyncMock(return_value=output_model.model_validate_json(response_json))
    return provider


# --- SentimentAgent ---


class TestSentimentAgent:
    @pytest.mark.asyncio
    async def test_returns_sentiment_report(self):
        provider = _mock_provider(SENTIMENT_RESPONSE_JSON, SentimentReport)
        agent = SentimentAgent(provider, "claude-haiku-4-5-20251001", LLMProvider.CLAUDE)

        result = await agent.run(SAMPLE_REDDIT_DIGEST)
        assert isinstance(result, SentimentReport)
        assert len(result.tickers) == 1
        assert result.tickers[0].ticker == "ASML.AS"
        assert result.tickers[0].sentiment_score == 0.72

    def test_properties(self):
        provider = AsyncMock()
        agent = SentimentAgent(provider, "model", LLMProvider.CLAUDE)
        assert agent.provider == LLMProvider.CLAUDE
        assert agent.stage == AgentStage.SENTIMENT
        assert agent.name == "claude-sentiment"

    @pytest.mark.asyncio
    async def test_prompt_loaded(self):
        provider = _mock_provider(SENTIMENT_RESPONSE_JSON, SentimentReport)
        agent = SentimentAgent(provider, "model", LLMProvider.CLAUDE)
        assert "sentiment" in agent._system_prompt.lower()
        assert "Reddit" in agent._system_prompt


# --- MarketAgent ---


class TestMarketAgent:
    @pytest.mark.asyncio
    async def test_returns_market_analysis(self):
        provider = _mock_provider(MARKET_RESPONSE_JSON, MarketAnalysis)
        agent = MarketAgent(provider, "claude-sonnet-4-5-20250929", LLMProvider.CLAUDE)

        result = await agent.run(
            {
                "sentiment": SAMPLE_SENTIMENT,
                "market_data": SAMPLE_MARKET_DATA,
            }
        )
        assert isinstance(result, MarketAnalysis)
        assert len(result.tickers) == 1
        assert result.tickers[0].fundamental_score == 8.5

    @pytest.mark.asyncio
    async def test_includes_sentiment_and_market_data(self):
        provider = _mock_provider(MARKET_RESPONSE_JSON, MarketAnalysis)
        agent = MarketAgent(provider, "model", LLMProvider.MINIMAX)

        await agent.run(
            {
                "sentiment": SAMPLE_SENTIMENT,
                "market_data": SAMPLE_MARKET_DATA,
            }
        )

        call_args = provider.generate.call_args
        user_msg = call_args[1]["user_message"]
        assert "ASML.AS" in user_msg
        assert "Sentiment Report" in user_msg
        assert "Market Data" in user_msg

    def test_properties(self):
        provider = AsyncMock()
        agent = MarketAgent(provider, "model", LLMProvider.MINIMAX)
        assert agent.provider == LLMProvider.MINIMAX
        assert agent.stage == AgentStage.MARKET


# --- TraderAgent ---


class TestTraderAgent:
    @pytest.mark.asyncio
    async def test_returns_daily_picks(self):
        provider = _mock_provider(TRADER_RESPONSE_JSON, DailyPicks)
        agent = TraderAgent(provider, "claude-opus-4-6", LLMProvider.CLAUDE)

        result = await agent.run(
            {
                "sentiment": SAMPLE_SENTIMENT,
                "market_analysis": SAMPLE_MARKET_ANALYSIS,
                "portfolio": [],
                "budget_eur": 10.0,
            }
        )
        assert isinstance(result, DailyPicks)
        assert len(result.picks) == 1
        assert result.picks[0].ticker == "ASML.AS"
        assert result.confidence == 0.75

    @pytest.mark.asyncio
    async def test_includes_all_context(self):
        provider = _mock_provider(TRADER_RESPONSE_JSON, DailyPicks)
        agent = TraderAgent(provider, "model", LLMProvider.CLAUDE)

        await agent.run(
            {
                "sentiment": SAMPLE_SENTIMENT,
                "market_analysis": SAMPLE_MARKET_ANALYSIS,
                "portfolio": [{"ticker": "SAP.DE", "quantity": "0.5"}],
                "budget_eur": 10.0,
            }
        )

        call_args = provider.generate.call_args
        user_msg = call_args[1]["user_message"]
        assert "Sentiment Report" in user_msg
        assert "Market Analysis" in user_msg
        assert "Current Portfolio" in user_msg
        assert "Daily Budget" in user_msg
        assert "10.0 EUR" in user_msg
        assert "SAP.DE" in user_msg

    @pytest.mark.asyncio
    async def test_accepts_research_report_input(self):
        provider = _mock_provider(TRADER_RESPONSE_JSON, DailyPicks)
        research = ResearchReport(
            analysis_date=date(2026, 2, 15),
            tickers=[
                ResearchFinding(
                    ticker="ASML.AS",
                    fundamental_score=8.5,
                    technical_score=7.0,
                    risk_score=3.0,
                    summary="Strong fundamentals.",
                )
            ],
            tool_calls_made=5,
        )
        agent = TraderAgent(provider, "model", LLMProvider.CLAUDE)

        result = await agent.run(
            {
                "sentiment": SAMPLE_SENTIMENT,
                "research": research,
                "portfolio": [],
                "budget_eur": 10.0,
            }
        )

        assert isinstance(result, DailyPicks)
        call_args = provider.generate.call_args
        user_msg = call_args[1]["user_message"]
        assert "Research Report" in user_msg

    def test_properties(self):
        provider = AsyncMock()
        agent = TraderAgent(provider, "model", LLMProvider.CLAUDE)
        assert agent.provider == LLMProvider.CLAUDE
        assert agent.stage == AgentStage.TRADER
        assert agent.name == "claude-trader"

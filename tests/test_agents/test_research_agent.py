from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.agents.research_agent import ResearchAgent
from src.models import (
    AgentStage,
    LLMProvider,
    ResearchFinding,
    ResearchReport,
    SentimentReport,
    TickerSentiment,
)

SAMPLE_SENTIMENT = SentimentReport(
    report_date=date(2026, 2, 15),
    tickers=[
        TickerSentiment(ticker="ASML.AS", mention_count=15, sentiment_score=0.7),
        TickerSentiment(ticker="SAP.DE", mention_count=8, sentiment_score=0.3),
    ],
    total_posts_analyzed=100,
    subreddits_scraped=["wallstreetbets", "investing"],
)

SAMPLE_RESEARCH = ResearchReport(
    analysis_date=date(2026, 2, 15),
    tickers=[
        ResearchFinding(
            ticker="ASML.AS",
            fundamental_score=8.5,
            technical_score=7.0,
            risk_score=3.0,
            summary="Strong pick.",
        )
    ],
    tool_calls_made=5,
)


class TestResearchAgent:
    @pytest.mark.asyncio
    async def test_returns_research_report(self):
        provider = AsyncMock()
        provider.generate_with_tools = AsyncMock(return_value=(SAMPLE_RESEARCH, 5))
        executor = AsyncMock()

        agent = ResearchAgent(provider, "claude-sonnet-4-5-20250929", LLMProvider.CLAUDE, executor)

        result = await agent.run({"sentiment": SAMPLE_SENTIMENT})

        assert isinstance(result, ResearchReport)
        assert len(result.tickers) == 1
        assert result.tickers[0].ticker == "ASML.AS"
        assert result.tool_calls_made == 5

    @pytest.mark.asyncio
    async def test_passes_sentiment_in_prompt(self):
        provider = AsyncMock()
        provider.generate_with_tools = AsyncMock(return_value=(SAMPLE_RESEARCH, 3))
        executor = AsyncMock()

        agent = ResearchAgent(provider, "model", LLMProvider.CLAUDE, executor)
        await agent.run({"sentiment": SAMPLE_SENTIMENT})

        call_args = provider.generate_with_tools.call_args
        user_msg = call_args[1]["user_message"]
        assert "Sentiment Report" in user_msg
        assert "ASML.AS" in user_msg

    def test_properties(self):
        provider = AsyncMock()
        executor = AsyncMock()
        agent = ResearchAgent(provider, "model", LLMProvider.CLAUDE, executor)
        assert agent.provider == LLMProvider.CLAUDE
        assert agent.stage == AgentStage.RESEARCH

    @pytest.mark.asyncio
    async def test_tools_are_passed(self):
        provider = AsyncMock()
        provider.generate_with_tools = AsyncMock(return_value=(SAMPLE_RESEARCH, 2))
        executor = AsyncMock()

        agent = ResearchAgent(provider, "model", LLMProvider.CLAUDE, executor)
        await agent.run({"sentiment": SAMPLE_SENTIMENT})

        call_args = provider.generate_with_tools.call_args
        assert call_args[1]["tools"] is not None
        assert len(call_args[1]["tools"]) > 0
        assert call_args[1]["tool_executor"] is executor

from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.agents.risk_agent import RiskReviewAgent
from src.db.models import (
    AgentStage,
    DailyPicks,
    LLMProvider,
    PickReview,
    ResearchFinding,
    ResearchReport,
    StockPick,
)

SAMPLE_PICKS = DailyPicks(
    llm=LLMProvider.CLAUDE,
    pick_date=date(2026, 2, 15),
    picks=[StockPick(ticker="ASML.AS", allocation_pct=60.0, reasoning="Strong pick")],
    confidence=0.8,
    market_summary="Markets looking good.",
)

SAMPLE_RESEARCH = ResearchReport(
    analysis_date=date(2026, 2, 15),
    tickers=[
        ResearchFinding(
            ticker="ASML.AS",
            fundamental_score=8.0,
            technical_score=7.0,
            risk_score=3.0,
            summary="Strong pick.",
        )
    ],
    tool_calls_made=12,
)

SAMPLE_REVIEW = PickReview(
    llm=LLMProvider.CLAUDE,
    pick_date=date(2026, 2, 15),
    picks=[StockPick(ticker="ASML.AS", allocation_pct=55.0, reasoning="Reduced alloc")],
    confidence=0.75,
    market_summary="Markets looking good.",
    risk_notes="Slightly reduced allocation due to concentration.",
    adjustments=["Reduced ASML.AS from 60% to 55%"],
)


class TestRiskReviewAgent:
    @pytest.mark.asyncio
    async def test_returns_pick_review(self):
        provider = AsyncMock()
        provider.generate = AsyncMock(return_value=SAMPLE_REVIEW)

        agent = RiskReviewAgent(provider, "claude-sonnet-4-5-20250929", LLMProvider.CLAUDE)

        result = await agent.run(
            {"picks": SAMPLE_PICKS, "research": SAMPLE_RESEARCH, "portfolio": []}
        )

        assert isinstance(result, PickReview)
        assert len(result.picks) == 1
        assert result.picks[0].ticker == "ASML.AS"
        assert result.risk_notes != ""
        assert len(result.adjustments) == 1

    @pytest.mark.asyncio
    async def test_includes_all_context_in_prompt(self):
        provider = AsyncMock()
        provider.generate = AsyncMock(return_value=SAMPLE_REVIEW)

        agent = RiskReviewAgent(provider, "model", LLMProvider.CLAUDE)
        await agent.run(
            {
                "picks": SAMPLE_PICKS,
                "research": SAMPLE_RESEARCH,
                "portfolio": [{"ticker": "SAP.DE"}],
            }
        )

        call_args = provider.generate.call_args
        user_msg = call_args[1]["user_message"]
        assert "Trading Picks" in user_msg
        assert "Research Report" in user_msg
        assert "Current Portfolio" in user_msg
        assert "ASML.AS" in user_msg
        assert "SAP.DE" in user_msg

    def test_properties(self):
        provider = AsyncMock()
        agent = RiskReviewAgent(provider, "model", LLMProvider.CLAUDE_AGGRESSIVE)
        assert agent.provider == LLMProvider.CLAUDE_AGGRESSIVE
        assert agent.stage == AgentStage.RISK

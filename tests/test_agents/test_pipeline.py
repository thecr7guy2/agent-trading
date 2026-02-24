from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.pipeline import AgentPipeline, PipelineOutput
from src.models import (
    DailyPicks,
    LLMProvider,
    ResearchFinding,
    ResearchReport,
    StockPick,
)

SAMPLE_RESEARCH = ResearchReport(
    tickers=[
        ResearchFinding(
            ticker="ASML.AS",
            pros=["CEO bought $500K", "RSI oversold at 28"],
            cons=["High P/E vs peers", "Revenue growth decelerating"],
            catalyst="Earnings call Q1 2026",
        )
    ],
)

SAMPLE_PICKS = DailyPicks(
    llm=LLMProvider.CLAUDE,
    pick_date=date(2026, 2, 15),
    picks=[StockPick(ticker="ASML.AS", allocation_pct=60.0, reasoning="Strong insider signal")],
    confidence=0.8,
    market_summary="Markets stable.",
)


def _mock_settings():
    s = MagicMock()
    s.anthropic_api_key = "test-anthropic-key"
    s.claude_sonnet_model = "claude-sonnet-4-6"
    s.claude_opus_model = "claude-opus-4-6"
    s.max_tool_rounds = 10
    return s


class TestAgentPipeline:
    @pytest.mark.asyncio
    async def test_full_pipeline_runs_research_then_decision(self):
        with patch("src.agents.pipeline.get_settings") as mock_settings:
            mock_settings.return_value = _mock_settings()
            pipeline = AgentPipeline()
            pipeline._research.run = AsyncMock(return_value=SAMPLE_RESEARCH)
            pipeline._trader.run = AsyncMock(return_value=SAMPLE_PICKS)

            result = await pipeline.run(
                enriched_digest={"candidates": []},
                portfolio=[],
                budget_eur=1000.0,
            )

            assert isinstance(result, PipelineOutput)
            assert result.picks.picks[0].ticker == "ASML.AS"
            assert result.research == SAMPLE_RESEARCH
            pipeline._research.run.assert_called_once()
            pipeline._trader.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_research_output_passed_to_trader(self):
        with patch("src.agents.pipeline.get_settings") as mock_settings:
            mock_settings.return_value = _mock_settings()
            pipeline = AgentPipeline()
            pipeline._research.run = AsyncMock(return_value=SAMPLE_RESEARCH)
            pipeline._trader.run = AsyncMock(return_value=SAMPLE_PICKS)

            digest = {"candidates": [{"ticker": "ASML.AS"}]}
            await pipeline.run(enriched_digest=digest, portfolio=[{"ticker": "OLD"}], budget_eur=500.0)

            trader_input = pipeline._trader.run.call_args[0][0]
            assert trader_input["research"] == SAMPLE_RESEARCH
            assert trader_input["portfolio"] == [{"ticker": "OLD"}]
            assert trader_input["budget_eur"] == 500.0

    @pytest.mark.asyncio
    async def test_run_date_applied_to_picks(self):
        with patch("src.agents.pipeline.get_settings") as mock_settings:
            mock_settings.return_value = _mock_settings()
            pipeline = AgentPipeline()
            pipeline._research.run = AsyncMock(return_value=SAMPLE_RESEARCH)
            pipeline._trader.run = AsyncMock(return_value=SAMPLE_PICKS)

            run_date = date(2026, 3, 1)
            result = await pipeline.run(
                enriched_digest={"candidates": []},
                run_date=run_date,
            )

            assert result.picks.pick_date == run_date

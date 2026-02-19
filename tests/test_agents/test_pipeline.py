from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.pipeline import AgentPipeline, PipelineOutput
from src.db.models import (
    DailyPicks,
    LLMProvider,
    PickReview,
    ResearchFinding,
    ResearchReport,
    SentimentReport,
    StockPick,
    TickerSentiment,
)

SAMPLE_SENTIMENT = SentimentReport(
    report_date=date(2026, 2, 15),
    tickers=[TickerSentiment(ticker="ASML.AS", mention_count=15, sentiment_score=0.7)],
    total_posts_analyzed=100,
    subreddits_scraped=["wallstreetbets"],
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

SAMPLE_PICKS = DailyPicks(
    llm=LLMProvider.CLAUDE,
    pick_date=date(2026, 2, 15),
    picks=[StockPick(ticker="ASML.AS", allocation_pct=60.0, reasoning="Strong pick")],
    confidence=0.8,
    market_summary="Markets looking good.",
)

SAMPLE_REVIEW = PickReview(
    llm=LLMProvider.CLAUDE,
    pick_date=date(2026, 2, 15),
    picks=[
        StockPick(ticker="ASML.AS", allocation_pct=55.0, reasoning="Strong pick, reduced alloc")
    ],
    confidence=0.75,
    market_summary="Markets looking good.",
    risk_notes="Slightly reduced allocation due to sector concentration.",
    adjustments=["Reduced ASML.AS from 60% to 55%"],
)


class TestAgentPipeline:
    @pytest.mark.asyncio
    async def test_full_pipeline_claude_with_tools(self):
        with patch("src.agents.pipeline.get_settings") as mock_settings:
            mock_settings.return_value = _mock_settings()

            mock_market_client = MagicMock()
            mock_trading_client = MagicMock()

            pipeline = AgentPipeline(
                LLMProvider.CLAUDE,
                market_data_client=mock_market_client,
                trading_client=mock_trading_client,
            )

            # Mock all four agents
            pipeline._sentiment.run = AsyncMock(return_value=SAMPLE_SENTIMENT)
            pipeline._research.run = AsyncMock(return_value=SAMPLE_RESEARCH)
            pipeline._trader.run = AsyncMock(return_value=SAMPLE_PICKS)
            pipeline._risk.run = AsyncMock(return_value=SAMPLE_REVIEW)

            result = await pipeline.run(
                signal_digest={"candidates": []},
                portfolio=[],
                budget_eur=10.0,
            )

            assert isinstance(result, PipelineOutput)
            assert result.picks.llm == LLMProvider.CLAUDE
            assert len(result.picks.picks) == 1
            assert result.picks.picks[0].ticker == "ASML.AS"
            assert result.research == SAMPLE_RESEARCH

            # Verify each stage was called
            pipeline._sentiment.run.assert_called_once()
            pipeline._research.run.assert_called_once()
            pipeline._trader.run.assert_called_once()
            pipeline._risk.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_pipeline_minimax(self):
        with patch("src.agents.pipeline.get_settings") as mock_settings:
            mock_settings.return_value = _mock_settings()

            mock_market_client = MagicMock()
            mock_trading_client = MagicMock()

            pipeline = AgentPipeline(
                LLMProvider.MINIMAX,
                market_data_client=mock_market_client,
                trading_client=mock_trading_client,
            )

            minimax_picks = DailyPicks(
                llm=LLMProvider.MINIMAX,
                pick_date=date(2026, 2, 15),
                picks=[StockPick(ticker="SAP.DE", allocation_pct=100.0, reasoning="All in")],
                confidence=0.6,
            )
            minimax_review = PickReview(
                llm=LLMProvider.MINIMAX,
                pick_date=date(2026, 2, 15),
                picks=[StockPick(ticker="SAP.DE", allocation_pct=100.0, reasoning="All in")],
                confidence=0.6,
            )

            pipeline._sentiment.run = AsyncMock(return_value=SAMPLE_SENTIMENT)
            pipeline._research.run = AsyncMock(return_value=SAMPLE_RESEARCH)
            pipeline._trader.run = AsyncMock(return_value=minimax_picks)
            pipeline._risk.run = AsyncMock(return_value=minimax_review)

            result = await pipeline.run(
                reddit_digest={},
                portfolio=[],
            )

            assert result.picks.llm == LLMProvider.MINIMAX
            assert result.picks.picks[0].ticker == "SAP.DE"
            assert result.research == SAMPLE_RESEARCH

    @pytest.mark.asyncio
    async def test_data_flows_between_stages(self):
        with patch("src.agents.pipeline.get_settings") as mock_settings:
            mock_settings.return_value = _mock_settings()

            mock_market_client = MagicMock()
            mock_trading_client = MagicMock()

            pipeline = AgentPipeline(
                LLMProvider.CLAUDE,
                market_data_client=mock_market_client,
                trading_client=mock_trading_client,
            )
            pipeline._sentiment.run = AsyncMock(return_value=SAMPLE_SENTIMENT)
            pipeline._research.run = AsyncMock(return_value=SAMPLE_RESEARCH)
            pipeline._trader.run = AsyncMock(return_value=SAMPLE_PICKS)
            pipeline._risk.run = AsyncMock(return_value=SAMPLE_REVIEW)

            await pipeline.run(
                signal_digest={"data": "test"},
                portfolio=[{"ticker": "OLD"}],
                budget_eur=5.0,
            )

            # Stage 1 receives signal digest
            sentiment_input = pipeline._sentiment.run.call_args[0][0]
            assert sentiment_input == {"data": "test"}

            # Stage 2 receives sentiment
            research_input = pipeline._research.run.call_args[0][0]
            assert research_input["sentiment"] == SAMPLE_SENTIMENT

            # Stage 3 receives sentiment + research + portfolio + budget
            trader_input = pipeline._trader.run.call_args[0][0]
            assert trader_input["sentiment"] == SAMPLE_SENTIMENT
            assert trader_input["research"] == SAMPLE_RESEARCH
            assert trader_input["portfolio"] == [{"ticker": "OLD"}]
            assert trader_input["budget_eur"] == 5.0

            # Stage 4 receives picks + research + portfolio
            risk_input = pipeline._risk.run.call_args[0][0]
            assert risk_input["picks"] == SAMPLE_PICKS
            assert risk_input["research"] == SAMPLE_RESEARCH
            assert risk_input["portfolio"] == [{"ticker": "OLD"}]

    @pytest.mark.asyncio
    async def test_pipeline_without_clients_uses_legacy(self):
        with patch("src.agents.pipeline.get_settings") as mock_settings:
            mock_settings.return_value = _mock_settings()
            pipeline = AgentPipeline(LLMProvider.CLAUDE)
            assert pipeline._research is None


def _mock_settings():
    s = MagicMock()
    s.anthropic_api_key = "test-anthropic-key"
    s.minimax_api_key = "test-minimax-key"
    s.minimax_base_url = "https://api.minimaxi.chat/v1"
    s.claude_haiku_model = "claude-haiku-4-5-20251001"
    s.claude_sonnet_model = "claude-sonnet-4-5-20250929"
    s.claude_opus_model = "claude-opus-4-6"
    s.minimax_model = "MiniMax-Text-01"
    s.max_tool_rounds = 8
    s.pipeline_timeout_seconds = 600
    return s

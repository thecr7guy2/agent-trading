from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.pipeline import AgentPipeline, PipelineOutput
from src.models import (
    DailyPicks,
    LLMProvider,
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

            # Mock all three agents (no separate risk stage)
            pipeline._sentiment.run = AsyncMock(return_value=SAMPLE_SENTIMENT)
            pipeline._research.run = AsyncMock(return_value=SAMPLE_RESEARCH)
            pipeline._trader.run = AsyncMock(return_value=SAMPLE_PICKS)

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

    @pytest.mark.asyncio
    async def test_full_pipeline_minimax(self):
        with patch("src.agents.pipeline.get_settings") as mock_settings:
            mock_settings.return_value = _mock_settings()

            mock_market_client = MagicMock()
            mock_trading_client = MagicMock()

            pipeline = AgentPipeline(
                LLMProvider.CLAUDE_AGGRESSIVE,
                market_data_client=mock_market_client,
                trading_client=mock_trading_client,
                strategy="aggressive",
            )

            aggressive_picks = DailyPicks(
                llm=LLMProvider.CLAUDE_AGGRESSIVE,
                pick_date=date(2026, 2, 15),
                picks=[StockPick(ticker="SAP.DE", allocation_pct=100.0, reasoning="All in")],
                confidence=0.6,
            )
            pipeline._sentiment.run = AsyncMock(return_value=SAMPLE_SENTIMENT)
            pipeline._research.run = AsyncMock(return_value=SAMPLE_RESEARCH)
            pipeline._trader.run = AsyncMock(return_value=aggressive_picks)

            result = await pipeline.run(
                reddit_digest={},
                portfolio=[],
            )

            assert result.picks.llm == LLMProvider.CLAUDE_AGGRESSIVE
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
    s.claude_sonnet_model = "claude-sonnet-4-6"
    s.claude_opus_model = "claude-opus-4-6"
    s.minimax_model = "MiniMax-Text-01"
    s.max_tool_rounds = 10
    s.pipeline_timeout_seconds = 600
    return s

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.pipeline import AgentPipeline
from src.db.models import (
    DailyPicks,
    LLMProvider,
    MarketAnalysis,
    SentimentReport,
    StockPick,
    TickerAnalysis,
    TickerSentiment,
)

SAMPLE_SENTIMENT = SentimentReport(
    report_date=date(2026, 2, 15),
    tickers=[TickerSentiment(ticker="ASML.AS", mention_count=15, sentiment_score=0.7)],
    total_posts_analyzed=100,
    subreddits_scraped=["wallstreetbets"],
)

SAMPLE_ANALYSIS = MarketAnalysis(
    analysis_date=date(2026, 2, 15),
    tickers=[
        TickerAnalysis(
            ticker="ASML.AS",
            fundamental_score=8.0,
            technical_score=7.0,
            risk_score=3.0,
        )
    ],
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
    async def test_full_pipeline_claude(self):
        with patch("src.agents.pipeline.get_settings") as mock_settings:
            mock_settings.return_value = _mock_settings()

            pipeline = AgentPipeline(LLMProvider.CLAUDE)

            # Mock all three agents
            pipeline._sentiment.run = AsyncMock(return_value=SAMPLE_SENTIMENT)
            pipeline._market.run = AsyncMock(return_value=SAMPLE_ANALYSIS)
            pipeline._trader.run = AsyncMock(return_value=SAMPLE_PICKS)

            result = await pipeline.run(
                reddit_digest={"tickers": []},
                market_data={"ASML.AS": {"price": 850}},
                portfolio=[],
                budget_eur=10.0,
            )

            assert isinstance(result, DailyPicks)
            assert result.llm == LLMProvider.CLAUDE
            assert len(result.picks) == 1
            assert result.picks[0].ticker == "ASML.AS"

            # Verify each stage was called
            pipeline._sentiment.run.assert_called_once()
            pipeline._market.run.assert_called_once()
            pipeline._trader.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_pipeline_minimax(self):
        with patch("src.agents.pipeline.get_settings") as mock_settings:
            mock_settings.return_value = _mock_settings()

            pipeline = AgentPipeline(LLMProvider.MINIMAX)

            minimax_picks = DailyPicks(
                llm=LLMProvider.MINIMAX,
                pick_date=date(2026, 2, 15),
                picks=[StockPick(ticker="SAP.DE", allocation_pct=100.0, reasoning="All in")],
                confidence=0.6,
            )

            pipeline._sentiment.run = AsyncMock(return_value=SAMPLE_SENTIMENT)
            pipeline._market.run = AsyncMock(return_value=SAMPLE_ANALYSIS)
            pipeline._trader.run = AsyncMock(return_value=minimax_picks)

            result = await pipeline.run(
                reddit_digest={},
                market_data={},
                portfolio=[],
            )

            assert result.llm == LLMProvider.MINIMAX
            assert result.picks[0].ticker == "SAP.DE"

    @pytest.mark.asyncio
    async def test_data_flows_between_stages(self):
        with patch("src.agents.pipeline.get_settings") as mock_settings:
            mock_settings.return_value = _mock_settings()

            pipeline = AgentPipeline(LLMProvider.CLAUDE)
            pipeline._sentiment.run = AsyncMock(return_value=SAMPLE_SENTIMENT)
            pipeline._market.run = AsyncMock(return_value=SAMPLE_ANALYSIS)
            pipeline._trader.run = AsyncMock(return_value=SAMPLE_PICKS)

            await pipeline.run(
                reddit_digest={"data": "test"},
                market_data={"ASML.AS": {}},
                portfolio=[{"ticker": "OLD"}],
                budget_eur=5.0,
            )

            # Stage 1 receives reddit digest
            sentiment_input = pipeline._sentiment.run.call_args[0][0]
            assert sentiment_input == {"data": "test"}

            # Stage 2 receives sentiment + market data
            market_input = pipeline._market.run.call_args[0][0]
            assert market_input["sentiment"] == SAMPLE_SENTIMENT
            assert "ASML.AS" in market_input["market_data"]

            # Stage 3 receives sentiment + analysis + portfolio + budget
            trader_input = pipeline._trader.run.call_args[0][0]
            assert trader_input["sentiment"] == SAMPLE_SENTIMENT
            assert trader_input["market_analysis"] == SAMPLE_ANALYSIS
            assert trader_input["portfolio"] == [{"ticker": "OLD"}]
            assert trader_input["budget_eur"] == 5.0


def _mock_settings():
    from unittest.mock import MagicMock

    s = MagicMock()
    s.anthropic_api_key = "test-anthropic-key"
    s.minimax_api_key = "test-minimax-key"
    s.minimax_base_url = "https://api.minimaxi.chat/v1"
    s.claude_haiku_model = "claude-haiku-4-5-20251001"
    s.claude_sonnet_model = "claude-sonnet-4-5-20250929"
    s.claude_opus_model = "claude-opus-4-6"
    s.minimax_model = "MiniMax-Text-01"
    return s

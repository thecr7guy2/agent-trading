import logging
from datetime import date

from src.agents.market_agent import MarketAgent
from src.agents.providers.claude import ClaudeProvider
from src.agents.providers.minimax import MiniMaxProvider
from src.agents.sentiment_agent import SentimentAgent
from src.agents.trader_agent import TraderAgent
from src.config import get_settings
from src.db.models import DailyPicks, LLMProvider

logger = logging.getLogger(__name__)


class AgentPipeline:
    def __init__(self, llm: LLMProvider):
        self._llm = llm
        settings = get_settings()

        if llm == LLMProvider.CLAUDE:
            provider = ClaudeProvider(api_key=settings.anthropic_api_key)
            sentiment_model = settings.claude_haiku_model
            market_model = settings.claude_sonnet_model
            trader_model = settings.claude_opus_model
        else:
            provider = MiniMaxProvider(
                api_key=settings.minimax_api_key,
                base_url=settings.minimax_base_url,
            )
            sentiment_model = settings.minimax_model
            market_model = settings.minimax_model
            trader_model = settings.minimax_model

        self._sentiment = SentimentAgent(provider, sentiment_model, llm)
        self._market = MarketAgent(provider, market_model, llm)
        self._trader = TraderAgent(provider, trader_model, llm)

    async def run(
        self,
        reddit_digest: dict,
        market_data: dict,
        portfolio: list,
        budget_eur: float = 10.0,
        run_date: date | None = None,
    ) -> DailyPicks:
        logger.info("[%s] Stage 1: Sentiment analysis", self._llm)
        sentiment = await self._sentiment.run(reddit_digest)
        logger.info(
            "[%s] Sentiment done — %d tickers identified", self._llm, len(sentiment.tickers)
        )

        logger.info("[%s] Stage 2: Market analysis", self._llm)
        analysis = await self._market.run(
            {
                "sentiment": sentiment,
                "market_data": market_data,
            }
        )
        logger.info(
            "[%s] Market analysis done — %d tickers scored", self._llm, len(analysis.tickers)
        )

        logger.info("[%s] Stage 3: Trading decisions", self._llm)
        picks = await self._trader.run(
            {
                "sentiment": sentiment,
                "market_analysis": analysis,
                "portfolio": portfolio,
                "budget_eur": budget_eur,
            }
        )
        picks.llm = self._llm
        if run_date is not None:
            picks.pick_date = run_date
        logger.info(
            "[%s] Trading done — %d picks, confidence %.2f",
            self._llm,
            len(picks.picks),
            picks.confidence,
        )

        return picks

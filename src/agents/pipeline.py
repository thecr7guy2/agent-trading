from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from src.agents.providers.claude import ClaudeProvider
from src.agents.providers.minimax import MiniMaxProvider
from src.agents.research_agent import ResearchAgent
from src.agents.sentiment_agent import SentimentAgent
from src.agents.tool_executor import ToolExecutor
from src.agents.tools import RESEARCH_TOOL_NAMES
from src.agents.trader_agent import TraderAgent
from src.config import get_settings
from src.models import LLMProvider, MarketAnalysis, PickReview, ResearchReport, SentimentReport

if TYPE_CHECKING:
    from src.orchestrator.mcp_client import MCPToolClient

logger = logging.getLogger(__name__)


@dataclass
class PipelineOutput:
    picks: PickReview
    research: ResearchReport | MarketAnalysis | None = None


class AgentPipeline:
    def __init__(
        self,
        llm: LLMProvider,
        market_data_client: MCPToolClient | None = None,
        trading_client: MCPToolClient | None = None,
        strategy: str = "conservative",
    ):
        self._llm = llm
        self._strategy = strategy
        settings = get_settings()

        # MiniMax: cheap data-gathering stages (1 & 2)
        self._minimax_provider = MiniMaxProvider(
            api_key=settings.minimax_api_key,
            base_url=settings.minimax_base_url,
        )
        self._minimax_model = settings.minimax_model
        minimax_provider = self._minimax_provider
        minimax_model = self._minimax_model

        # Claude: decision stage (3)
        claude_provider = ClaudeProvider(api_key=settings.anthropic_api_key)
        trader_model = settings.claude_opus_model

        max_tool_rounds = settings.max_tool_rounds

        # Build tool executor for research stage if MCP client is provided
        research_executor = None
        if market_data_client is not None:
            research_executor = ToolExecutor(market_data_client, RESEARCH_TOOL_NAMES)

        # Stage 1: Sentiment — MiniMax (no tools)
        self._sentiment = SentimentAgent(minimax_provider, minimax_model, llm)

        # Stage 2: Research — MiniMax with tools
        if research_executor is not None:
            self._research = ResearchAgent(
                minimax_provider, minimax_model, llm, research_executor, max_tool_rounds
            )
        else:
            self._research = None

        # Stage 3: Trader — Claude Opus (final decision, no tools needed)
        self._trader = TraderAgent(
            claude_provider,
            trader_model,
            llm,
            tool_executor=None,
            max_tool_rounds=5,
            strategy=strategy,
        )

    async def run_research(
        self,
        digest_input: dict,
        market_data: dict | None = None,
    ) -> tuple[SentimentReport, ResearchReport | MarketAnalysis]:
        """Stages 1-2: shared data-gathering (MiniMax). Run once and reuse across strategies."""
        # Stage 1: Sentiment
        logger.info("Stage 1: Sentiment analysis (shared)")
        sentiment = await self._sentiment.run(digest_input)
        logger.info("Sentiment done — %d tickers identified", len(sentiment.tickers))

        # Stage 2: Research or legacy market analysis
        if self._research is not None:
            logger.info("Stage 2: Research (with tools, shared)")
            research = await self._research.run({"sentiment": sentiment})
            logger.info(
                "Research done — %d tickers, %d tool calls",
                len(research.tickers),
                research.tool_calls_made,
            )
        else:
            from src.agents.market_agent import MarketAgent

            logger.info("Stage 2: Market analysis (legacy, no tools, shared)")
            market_agent = MarketAgent(self._minimax_provider, self._minimax_model, self._llm)
            research = await market_agent.run(
                {"sentiment": sentiment, "market_data": market_data or {}}
            )
            logger.info("Market analysis done — %d tickers scored", len(research.tickers))

        return sentiment, research

    async def run_decision(
        self,
        sentiment: SentimentReport,
        research: ResearchReport | MarketAnalysis,
        portfolio: list | None = None,
        budget_eur: float = 10.0,
        run_date: date | None = None,
    ) -> PipelineOutput:
        """Stages 3-4: strategy-specific decisions (Claude). Run per strategy."""
        portfolio = portfolio or []

        # Stage 3: Trading decisions
        logger.info("[%s] Stage 3: Trading decisions", self._llm)
        picks = await self._trader.run(
            {
                "sentiment": sentiment,
                "research": research,
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

        # Wrap DailyPicks → PickReview (no risk stage)
        reviewed = PickReview(
            llm=picks.llm,
            pick_date=picks.pick_date,
            picks=picks.picks,
            sell_recommendations=picks.sell_recommendations,
            confidence=picks.confidence,
            market_summary=picks.market_summary,
        )
        return PipelineOutput(picks=reviewed, research=research)

    async def run(
        self,
        reddit_digest: dict | None = None,
        market_data: dict | None = None,
        portfolio: list | None = None,
        budget_eur: float = 10.0,
        run_date: date | None = None,
        signal_digest: dict | None = None,
    ) -> PipelineOutput:
        """Full pipeline: stages 1-4 in sequence. Used for single-strategy runs."""
        digest_input = signal_digest or reddit_digest or {}
        sentiment, research = await self.run_research(digest_input, market_data)
        return await self.run_decision(sentiment, research, portfolio or [], budget_eur, run_date)


class _MergedToolExecutor(ToolExecutor):
    """Routes tool calls to the appropriate MCP client."""

    def __init__(
        self,
        market_client: MCPToolClient,
        trading_client: MCPToolClient,
        allowed_tools: set[str],
    ):
        self._market_client = market_client
        self._trading_client = trading_client
        self._allowed = allowed_tools
        # Tools that live on the trading server
        self._trading_tools = {"get_positions", "get_cash"}

    async def execute(self, tool_name: str, args: dict) -> dict:
        if tool_name not in self._allowed:
            return {"error": f"Tool '{tool_name}' is not available"}
        if tool_name in self._trading_tools:
            client = self._trading_client
        else:
            client = self._market_client

        import asyncio

        try:
            return await asyncio.wait_for(client.call_tool(tool_name, args), timeout=30.0)
        except TimeoutError:
            return {"error": f"Tool '{tool_name}' timed out"}
        except Exception as e:
            return {"error": str(e)}

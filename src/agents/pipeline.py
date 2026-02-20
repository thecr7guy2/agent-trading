from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from src.agents.providers.claude import ClaudeProvider
from src.agents.providers.minimax import MiniMaxProvider
from src.agents.research_agent import ResearchAgent
from src.agents.risk_agent import RiskReviewAgent
from src.agents.sentiment_agent import SentimentAgent
from src.agents.tool_executor import ToolExecutor
from src.agents.tools import RESEARCH_TOOL_NAMES
from src.agents.trader_agent import TraderAgent
from src.config import get_settings
from src.db.models import LLMProvider, MarketAnalysis, PickReview, ResearchReport

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

        # Claude: decision stages (3 & 4)
        claude_provider = ClaudeProvider(api_key=settings.anthropic_api_key)
        trader_model = settings.claude_opus_model
        risk_model = settings.claude_sonnet_model

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

        # Stage 4: Risk Review — Claude Haiku (no tools)
        self._risk = RiskReviewAgent(claude_provider, risk_model, llm)

    async def run(
        self,
        reddit_digest: dict | None = None,
        market_data: dict | None = None,
        portfolio: list | None = None,
        budget_eur: float = 10.0,
        run_date: date | None = None,
        signal_digest: dict | None = None,
    ) -> PipelineOutput:
        digest_input = signal_digest or reddit_digest or {}
        portfolio = portfolio or []

        # Stage 1: Sentiment analysis
        logger.info("[%s] Stage 1: Sentiment analysis", self._llm)
        sentiment = await self._sentiment.run(digest_input)
        logger.info(
            "[%s] Sentiment done — %d tickers identified", self._llm, len(sentiment.tickers)
        )

        # Stage 2: Research (tool-calling) or Market analysis (legacy fallback)
        if self._research is not None:
            logger.info("[%s] Stage 2: Research (with tools)", self._llm)
            research = await self._research.run({"sentiment": sentiment})
            logger.info(
                "[%s] Research done — %d tickers, %d tool calls",
                self._llm,
                len(research.tickers),
                research.tool_calls_made,
            )
        else:
            # Legacy fallback: import MarketAgent for backward compat
            from src.agents.market_agent import MarketAgent

            logger.info("[%s] Stage 2: Market analysis (legacy, no tools)", self._llm)
            market_agent = MarketAgent(self._minimax_provider, self._minimax_model, self._llm)
            market_data = market_data or {}
            research = await market_agent.run({"sentiment": sentiment, "market_data": market_data})
            logger.info(
                "[%s] Market analysis done — %d tickers scored",
                self._llm,
                len(research.tickers),
            )

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

        # Stage 4: Risk review
        logger.info("[%s] Stage 4: Risk review", self._llm)
        reviewed = await self._risk.run(
            {
                "picks": picks,
                "research": research,
                "portfolio": portfolio,
            }
        )
        reviewed.llm = self._llm
        if run_date is not None:
            reviewed.pick_date = run_date
        logger.info(
            "[%s] Risk review done — %d picks approved, %d vetoed, confidence %.2f",
            self._llm,
            len(reviewed.picks),
            len(reviewed.vetoed_tickers),
            reviewed.confidence,
        )

        return PipelineOutput(picks=reviewed, research=research)


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
        self._trading_tools = {"get_portfolio", "get_positions", "get_trade_history"}

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

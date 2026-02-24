from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from src.agents.providers.claude import ClaudeProvider
from src.agents.research_agent import ResearchAgent
from src.agents.trader_agent import TraderAgent
from src.config import get_settings
from src.models import PickReview, ResearchReport

logger = logging.getLogger(__name__)


@dataclass
class PipelineOutput:
    picks: PickReview
    research: ResearchReport | None = None


class AgentPipeline:
    def __init__(self):
        settings = get_settings()

        # Claude provider shared by both stages
        claude_provider = ClaudeProvider(api_key=settings.anthropic_api_key)

        # Claude Sonnet: analyst stage (research — pros/cons/catalyst, no verdict, no tools)
        self._research = ResearchAgent(claude_provider, settings.claude_sonnet_model)

        # Claude Opus: portfolio manager stage (final buy decisions, no tools)
        self._trader = TraderAgent(
            claude_provider,
            settings.claude_opus_model,
            tool_executor=None,
            max_tool_rounds=0,
        )

    async def run_research(self, enriched_digest: dict) -> ResearchReport | None:
        """Stage 1: Claude Sonnet analyst — produces pros/cons/catalyst per ticker, no verdict."""
        logger.info(
            "Research stage: Claude Sonnet analyst (%d tickers)",
            len(enriched_digest.get("candidates", [])),
        )
        research = await self._research.run(enriched_digest)
        logger.info("Research done — %d tickers analysed", len(research.tickers))
        return research

    async def run_decision(
        self,
        research: ResearchReport | None,
        enriched_digest: dict,
        portfolio: list | None = None,
        budget_eur: float = 1000.0,
        run_date: date | None = None,
    ) -> PipelineOutput:
        """Stage 2: Claude Opus portfolio manager — independent buy decision."""
        portfolio = portfolio or []

        logger.info("Decision stage: Claude Opus (budget €%.0f)", budget_eur)
        picks = await self._trader.run(
            {
                "research": research,
                "digest": enriched_digest,
                "portfolio": portfolio,
                "budget_eur": budget_eur,
            }
        )

        if run_date is not None:
            picks.pick_date = run_date

        logger.info("Decision done — %d picks, confidence %.2f", len(picks.picks), picks.confidence)

        reviewed = PickReview(
            pick_date=picks.pick_date,
            picks=picks.picks,
            sell_recommendations=picks.sell_recommendations,
            confidence=picks.confidence,
            market_summary=picks.market_summary,
        )
        return PipelineOutput(picks=reviewed, research=research)

    async def run(
        self,
        enriched_digest: dict,
        portfolio: list | None = None,
        budget_eur: float = 1000.0,
        run_date: date | None = None,
    ) -> PipelineOutput:
        """Full pipeline: research → decision."""
        research = await self.run_research(enriched_digest)
        return await self.run_decision(
            research, enriched_digest, portfolio or [], budget_eur, run_date
        )

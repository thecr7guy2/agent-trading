from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from src.agents.providers.claude import ClaudeProvider
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

        claude_provider = ClaudeProvider(api_key=settings.anthropic_api_key)

        self._trader = TraderAgent(
            claude_provider,
            settings.claude_opus_model,
            tool_executor=None,
            max_tool_rounds=0,
        )

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
        """Full pipeline: decision only (research stage removed — candidates go straight to Opus)."""
        return await self.run_decision(None, enriched_digest, portfolio or [], budget_eur, run_date)

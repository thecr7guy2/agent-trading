import json
from pathlib import Path

from src.agents.base_agent import BaseAgent
from src.agents.providers.claude import ClaudeProvider
from src.agents.providers.minimax import MiniMaxProvider
from src.db.models import (
    AgentStage,
    DailyPicks,
    LLMProvider,
    PickReview,
    ResearchReport,
)

PROMPT_PATH = Path(__file__).parent / "prompts" / "risk_review.md"


class RiskReviewAgent(BaseAgent):
    def __init__(
        self,
        provider: ClaudeProvider | MiniMaxProvider,
        model: str,
        llm: LLMProvider,
    ):
        self._provider = provider
        self._model = model
        self._llm = llm
        self._system_prompt = PROMPT_PATH.read_text()

    @property
    def provider(self) -> LLMProvider:
        return self._llm

    @property
    def stage(self) -> AgentStage:
        return AgentStage.RISK

    async def run(self, input_data: dict) -> PickReview:
        picks: DailyPicks = input_data["picks"]
        research: ResearchReport = input_data["research"]
        portfolio: list = input_data.get("portfolio", [])

        user_message = (
            f"You are reviewing picks from the **{self._llm.value}** LLM provider.\n\n"
            "## Trading Picks (to review)\n\n"
            f"{picks.model_dump_json(indent=2)}\n\n"
            "## Research Report\n\n"
            f"{research.model_dump_json(indent=2)}\n\n"
            "## Current Portfolio\n\n"
            f"{json.dumps(portfolio, indent=2, default=str)}\n\n"
            "Review these picks and apply risk management rules. "
            "Output your reviewed picks with any adjustments."
        )
        return await self._provider.generate(
            model=self._model,
            system_prompt=self._system_prompt,
            user_message=user_message,
            output_model=PickReview,
        )

import json
from pathlib import Path

from src.agents.base_agent import BaseAgent
from src.agents.providers.claude import ClaudeProvider
from src.agents.providers.minimax import MiniMaxProvider
from src.db.models import (
    AgentStage,
    DailyPicks,
    LLMProvider,
    MarketAnalysis,
    SentimentReport,
)

PROMPT_PATH = Path(__file__).parent / "prompts" / "trader.md"


class TraderAgent(BaseAgent):
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
        return AgentStage.TRADER

    async def run(self, input_data: dict) -> DailyPicks:
        sentiment: SentimentReport = input_data["sentiment"]
        market_analysis: MarketAnalysis = input_data["market_analysis"]
        portfolio: list = input_data.get("portfolio", [])
        budget_eur: float = input_data.get("budget_eur", 10.0)

        user_message = (
            f"You are the **{self._llm.value}** LLM provider.\n\n"
            "## Sentiment Report\n\n"
            f"{sentiment.model_dump_json(indent=2)}\n\n"
            "## Market Analysis\n\n"
            f"{market_analysis.model_dump_json(indent=2)}\n\n"
            "## Current Portfolio\n\n"
            f"{json.dumps(portfolio, indent=2, default=str)}\n\n"
            f"## Daily Budget\n\n{budget_eur} EUR\n\n"
            f"Today's date is {sentiment.report_date}."
        )
        return await self._provider.generate(
            model=self._model,
            system_prompt=self._system_prompt,
            user_message=user_message,
            output_model=DailyPicks,
        )

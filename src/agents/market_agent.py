import json
from pathlib import Path

from src.agents.base_agent import BaseAgent
from src.agents.providers.claude import ClaudeProvider
from src.agents.providers.minimax import MiniMaxProvider
from src.db.models import AgentStage, LLMProvider, MarketAnalysis, SentimentReport

PROMPT_PATH = Path(__file__).parent / "prompts" / "market_analysis.md"


class MarketAgent(BaseAgent):
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
        return AgentStage.MARKET

    async def run(self, input_data: dict) -> MarketAnalysis:
        sentiment: SentimentReport = input_data["sentiment"]
        market_data: dict = input_data["market_data"]

        user_message = (
            "## Sentiment Report\n\n"
            f"{sentiment.model_dump_json(indent=2)}\n\n"
            "## Market Data\n\n"
            f"{json.dumps(market_data, indent=2, default=str)}"
        )
        return await self._provider.generate(
            model=self._model,
            system_prompt=self._system_prompt,
            user_message=user_message,
            output_model=MarketAnalysis,
        )

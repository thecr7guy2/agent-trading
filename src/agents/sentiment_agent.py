import json
from pathlib import Path

from src.agents.base_agent import BaseAgent
from src.agents.providers.claude import ClaudeProvider
from src.agents.providers.minimax import MiniMaxProvider
from src.db.models import AgentStage, LLMProvider, SentimentReport

PROMPT_PATH = Path(__file__).parent / "prompts" / "sentiment.md"


class SentimentAgent(BaseAgent):
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
        return AgentStage.SENTIMENT

    async def run(self, input_data: dict) -> SentimentReport:
        user_message = (
            "Here is today's Reddit digest data. Analyze it and produce a sentiment report.\n\n"
            f"{json.dumps(input_data, indent=2, default=str)}"
        )
        return await self._provider.generate(
            model=self._model,
            system_prompt=self._system_prompt,
            user_message=user_message,
            output_model=SentimentReport,
        )

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src.agents.base_agent import BaseAgent
from src.agents.providers.claude import ClaudeProvider
from src.agents.providers.minimax import MiniMaxProvider
from src.agents.tools import RESEARCH_TOOLS, to_claude_tools, to_openai_tools
from src.db.models import AgentStage, LLMProvider, ResearchReport, SentimentReport

if TYPE_CHECKING:
    from src.agents.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "research.md"


class ResearchAgent(BaseAgent):
    def __init__(
        self,
        provider: ClaudeProvider | MiniMaxProvider,
        model: str,
        llm: LLMProvider,
        tool_executor: ToolExecutor,
        max_tool_rounds: int = 15,
    ):
        self._provider = provider
        self._model = model
        self._llm = llm
        self._tool_executor = tool_executor
        self._max_tool_rounds = max_tool_rounds
        self._system_prompt = PROMPT_PATH.read_text()

        if isinstance(provider, ClaudeProvider):
            self._tools = to_claude_tools(RESEARCH_TOOLS)
        else:
            self._tools = to_openai_tools(RESEARCH_TOOLS)

    @property
    def provider(self) -> LLMProvider:
        return self._llm

    @property
    def stage(self) -> AgentStage:
        return AgentStage.RESEARCH

    async def run(self, input_data: dict) -> ResearchReport:
        sentiment: SentimentReport = input_data["sentiment"]

        user_message = (
            "## Sentiment Report\n\n"
            f"{sentiment.model_dump_json(indent=2)}\n\n"
            "Use the available tools to research the most promising tickers from this report. "
            "Focus on the top 8-10 candidates with the strongest signals."
        )

        report, tool_calls = await self._provider.generate_with_tools(
            model=self._model,
            system_prompt=self._system_prompt,
            user_message=user_message,
            output_model=ResearchReport,
            tools=self._tools,
            tool_executor=self._tool_executor,
            max_tool_rounds=self._max_tool_rounds,
        )
        report.tool_calls_made = tool_calls
        logger.info(
            "[%s] Research complete — %d tickers researched, %d tool calls",
            self._llm,
            len(report.tickers),
            tool_calls,
        )
        return report

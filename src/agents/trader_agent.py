from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src.agents.base_agent import BaseAgent
from src.agents.providers.claude import ClaudeProvider
from src.agents.providers.minimax import MiniMaxProvider
from src.agents.tools import TRADER_TOOLS, to_claude_tools, to_openai_tools
from src.db.models import (
    AgentStage,
    DailyPicks,
    LLMProvider,
    SentimentReport,
)

if TYPE_CHECKING:
    from src.agents.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "trader.md"


class TraderAgent(BaseAgent):
    def __init__(
        self,
        provider: ClaudeProvider | MiniMaxProvider,
        model: str,
        llm: LLMProvider,
        tool_executor: ToolExecutor | None = None,
        max_tool_rounds: int = 5,
    ):
        self._provider = provider
        self._model = model
        self._llm = llm
        self._tool_executor = tool_executor
        self._max_tool_rounds = max_tool_rounds
        self._system_prompt = PROMPT_PATH.read_text()

        if tool_executor is not None:
            if isinstance(provider, ClaudeProvider):
                self._tools = to_claude_tools(TRADER_TOOLS)
            else:
                self._tools = to_openai_tools(TRADER_TOOLS)
        else:
            self._tools = []

    @property
    def provider(self) -> LLMProvider:
        return self._llm

    @property
    def stage(self) -> AgentStage:
        return AgentStage.TRADER

    async def run(self, input_data: dict) -> DailyPicks:
        sentiment: SentimentReport = input_data["sentiment"]
        # Support both ResearchReport (Phase 8) and MarketAnalysis (legacy)
        research = input_data.get("research") or input_data.get("market_analysis")
        portfolio: list = input_data.get("portfolio", [])
        budget_eur: float = input_data.get("budget_eur", 10.0)

        research_label = "Research Report" if "research" in input_data else "Market Analysis"
        user_message = (
            f"You are the **{self._llm.value}** LLM provider.\n\n"
            "## Sentiment Report\n\n"
            f"{sentiment.model_dump_json(indent=2)}\n\n"
            f"## {research_label}\n\n"
            f"{research.model_dump_json(indent=2)}\n\n"
            "## Current Portfolio\n\n"
            f"{json.dumps(portfolio, indent=2, default=str)}\n\n"
            f"## Daily Budget\n\n{budget_eur} EUR\n\n"
            f"Today's date is {sentiment.report_date}."
        )

        if self._tool_executor and self._tools:
            picks, tool_calls = await self._provider.generate_with_tools(
                model=self._model,
                system_prompt=self._system_prompt,
                user_message=user_message,
                output_model=DailyPicks,
                tools=self._tools,
                tool_executor=self._tool_executor,
                max_tool_rounds=self._max_tool_rounds,
            )
            logger.info("[%s] Trader used %d tool calls for verification", self._llm, tool_calls)
        else:
            picks = await self._provider.generate(
                model=self._model,
                system_prompt=self._system_prompt,
                user_message=user_message,
                output_model=DailyPicks,
            )
        return picks

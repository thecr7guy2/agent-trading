from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from src.agents.base_agent import BaseAgent
from src.agents.providers.claude import ClaudeProvider
from src.agents.providers.minimax import MiniMaxProvider
from src.models import AgentStage, DailyPicks, LLMProvider, ResearchReport

if TYPE_CHECKING:
    from src.agents.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "trader_aggressive.md"


class TraderAgent(BaseAgent):
    def __init__(
        self,
        provider: ClaudeProvider | MiniMaxProvider,
        model: str,
        tool_executor: ToolExecutor | None = None,
        max_tool_rounds: int = 0,
    ):
        self._provider = provider
        self._model = model
        self._tool_executor = tool_executor
        self._max_tool_rounds = max_tool_rounds
        self._system_prompt = _PROMPT_PATH.read_text()

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.CLAUDE

    @property
    def stage(self) -> AgentStage:
        return AgentStage.TRADER

    @staticmethod
    def _render_research(research: ResearchReport) -> str:
        """Render MiniMax research notes as analyst context — pros/cons/catalyst only."""
        lines = []
        for f in research.tickers:
            lines.append(f"**{f.ticker}**")
            if f.pros:
                lines.append("  Analyst pros:")
                for pro in f.pros:
                    lines.append(f"    - {pro}")
            if f.cons:
                lines.append("  Analyst cons:")
                for con in f.cons:
                    lines.append(f"    - {con}")
            if f.catalyst:
                lines.append(f"  Catalyst: {f.catalyst}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_candidates(candidates: list[dict]) -> str:
        """Render the enriched insider candidates as structured data for Claude."""
        lines = []
        for c in candidates:
            ticker = c.get("ticker", "")
            lines.append(f"### {ticker} — {c.get('company', '')}")
            lines.append(f"Conviction score: {c.get('conviction_score', 0):.1f}")
            lines.append(
                f"Insiders buying: {c.get('insider_count', 1)} | C-suite present: {c.get('is_csuite_present', False)} | Cluster buy: {c.get('is_cluster', False)}"
            )
            lines.append(f"Max stake increase (ΔOwn): {c.get('max_delta_own_pct', 0):.1f}%")
            lines.append(f"Total insider $ spent: ${c.get('total_value_usd', 0):,.0f}")

            insider_names = c.get("insiders", [])
            if insider_names:
                # Include titles from transactions
                txns = c.get("transactions", [])
                insider_with_titles = [
                    f"{tx.get('insider_name', '')} ({tx.get('title', '')})" for tx in txns[:3]
                ]
                lines.append(f"Insiders: {' | '.join(insider_with_titles)}")

            insider_history = c.get("insider_history", {})
            if insider_history:
                lines.append(
                    f"Buy history (30/60/90d): {insider_history.get('buys_30d', 0)} / {insider_history.get('buys_60d', 0)} / {insider_history.get('buys_90d', 0)} | Accelerating: {insider_history.get('accelerating', False)}"
                )

            returns = c.get("returns", {})
            if returns:
                r1m = returns.get("return_1m")
                r6m = returns.get("return_6m")
                r1y = returns.get("return_1y")
                lines.append(
                    f"Price returns: 1m={f'{r1m * 100:.1f}%' if r1m is not None else 'N/A'} | 6m={f'{r6m * 100:.1f}%' if r6m is not None else 'N/A'} | 1y={f'{r1y * 100:.1f}%' if r1y is not None else 'N/A'}"
                )

            fundamentals = c.get("fundamentals", {})
            if fundamentals:
                lines.append(
                    f"Sector: {fundamentals.get('sector', 'N/A')} | Industry: {fundamentals.get('industry', 'N/A')}"
                )
                lines.append(
                    f"Market cap: {fundamentals.get('market_cap', 'N/A')} | P/E: {fundamentals.get('pe_ratio', 'N/A')} | EPS: {fundamentals.get('eps', 'N/A')}"
                )
                lines.append(
                    f"52w range: {fundamentals.get('fifty_two_week_low', 'N/A')} – {fundamentals.get('fifty_two_week_high', 'N/A')}"
                )
                lines.append(
                    f"Profit margin: {fundamentals.get('profit_margin', 'N/A')} | D/E: {fundamentals.get('debt_to_equity', 'N/A')}"
                )

            technicals = c.get("technicals", {})
            if technicals:
                macd = technicals.get("macd") or {}
                bb = technicals.get("bollinger_bands") or {}
                lines.append(
                    f"RSI: {technicals.get('rsi', 'N/A')} | MACD histogram: {macd.get('histogram', 'N/A')}"
                )
                lines.append(
                    f"Bollinger: upper={bb.get('upper', 'N/A')} mid={bb.get('middle', 'N/A')} lower={bb.get('lower', 'N/A')}"
                )

            news = c.get("news", [])
            if news:
                for n in news[:3]:
                    lines.append(f"News: {n.get('title', '')}")

            reddit = c.get("reddit", {})
            if reddit:
                lines.append(
                    f"Reddit mentions: {reddit.get('mentions', 0)} | Sentiment: {reddit.get('sentiment_score', 'N/A')}"
                )

            earnings = c.get("earnings", {})
            if earnings and earnings.get("earnings"):
                lines.append(f"Upcoming earnings: {json.dumps(earnings.get('earnings', {}), default=str)}")

            lines.append("")
        return "\n".join(lines)

    async def run(self, input_data: dict) -> DailyPicks:
        research: ResearchReport | None = input_data.get("research")
        enriched_digest: dict = input_data.get("digest", {})
        portfolio: list = input_data.get("portfolio", [])
        budget_eur: float = input_data.get("budget_eur", 1000.0)

        candidates = enriched_digest.get("candidates", [])
        candidates_text = self._render_candidates(candidates)

        research_text = ""
        if research and research.tickers:
            research_text = (
                "## Independent Analyst Notes (MiniMax)\n"
                "_These are factual observations from a separate model. "
                "Use them as context only — form your own investment thesis._\n\n"
                + self._render_research(research)
            )

        today = date.today().isoformat()
        user_message = (
            f"Date: {today}\n\n"
            "## Insider-Identified Candidates\n\n"
            f"{candidates_text}\n\n"
            f"{research_text}\n\n"
            "## Current Portfolio\n\n"
            f"{json.dumps(portfolio, indent=2, default=str)}\n\n"
            f"## Budget\n\n€{budget_eur:.0f} to deploy this run."
        )

        picks = await self._provider.generate(
            model=self._model,
            system_prompt=self._system_prompt,
            user_message=user_message,
            output_model=DailyPicks,
        )
        return picks

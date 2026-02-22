from __future__ import annotations

import json
import logging
from pathlib import Path

from src.agents.base_agent import BaseAgent
from src.agents.providers.claude import ClaudeProvider
from src.agents.providers.minimax import MiniMaxProvider
from src.models import AgentStage, LLMProvider, ResearchReport

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "research.md"


class ResearchAgent(BaseAgent):
    def __init__(
        self,
        provider: ClaudeProvider | MiniMaxProvider,
        model: str,
    ):
        self._provider = provider
        self._model = model
        self._system_prompt = PROMPT_PATH.read_text()

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.MINIMAX

    @property
    def stage(self) -> AgentStage:
        return AgentStage.RESEARCH

    async def run(self, enriched_digest: dict) -> ResearchReport:
        """
        Receives the enriched insider digest (25 candidates with yfinance data,
        news, OpenInsider history) and produces per-ticker pros/cons/catalyst.
        No scores, no BUY/SELL verdict — analyst role only.
        """
        # Cap at 12 — MiniMax truncates JSON output beyond ~12 candidates
        candidates = enriched_digest.get("candidates", [])[:12]

        # Render candidates as structured context for MiniMax
        candidate_lines = []
        for c in candidates:
            ticker = c.get("ticker", "")
            lines = [f"### {ticker} — {c.get('company', '')}"]
            lines.append(
                f"Conviction score: {c.get('conviction_score', 0):.1f} | Insiders: {c.get('insider_count', 1)} | C-suite present: {c.get('is_csuite_present', False)}"
            )
            lines.append(
                f"Max ΔOwn: {c.get('max_delta_own_pct', 0):.1f}% | Total insider value: ${c.get('total_value_usd', 0):,.0f}"
            )
            lines.append(f"Cluster buy: {c.get('is_cluster', False)}")

            insider_history = c.get("insider_history", {})
            if insider_history:
                lines.append(
                    f"Insider history (30/60/90d): {insider_history.get('buys_30d', 0)} / {insider_history.get('buys_60d', 0)} / {insider_history.get('buys_90d', 0)} buys | Accelerating: {insider_history.get('accelerating', False)}"
                )

            returns = c.get("returns", {})
            if returns:
                r1m = returns.get("return_1m")
                r6m = returns.get("return_6m")
                r1y = returns.get("return_1y")
                lines.append(
                    f"Returns: 1m={f'{r1m * 100:.1f}%' if r1m is not None else 'N/A'} | 6m={f'{r6m * 100:.1f}%' if r6m is not None else 'N/A'} | 1y={f'{r1y * 100:.1f}%' if r1y is not None else 'N/A'}"
                )

            fundamentals = c.get("fundamentals", {})
            if fundamentals:
                lines.append(
                    f"Sector: {fundamentals.get('sector', 'N/A')} | Market cap: {fundamentals.get('market_cap', 'N/A')} | P/E: {fundamentals.get('pe_ratio', 'N/A')}"
                )
                lines.append(
                    f"52w high: {fundamentals.get('fifty_two_week_high', 'N/A')} | 52w low: {fundamentals.get('fifty_two_week_low', 'N/A')}"
                )

            technicals = c.get("technicals", {})
            if technicals:
                lines.append(
                    f"RSI: {technicals.get('rsi', 'N/A')} | MACD histogram: {technicals.get('macd', {}).get('histogram', 'N/A') if technicals.get('macd') else 'N/A'}"
                )

            news = c.get("news", [])
            if news:
                headlines = " | ".join(n.get("title", "") for n in news[:3])
                lines.append(f"Recent news: {headlines}")

            earnings = c.get("earnings", {})
            if earnings and earnings.get("earnings"):
                lines.append(f"Earnings: {json.dumps(earnings.get('earnings', {}), default=str)}")

            lines.append("")
            candidate_lines.append("\n".join(lines))

        candidates_text = "\n".join(candidate_lines)

        user_message = (
            f"The following {len(candidates)} stocks have been identified by insider buying signals. "
            "Analyse each one and provide your research findings.\n\n"
            f"{candidates_text}"
        )

        report = await self._provider.generate(
            model=self._model,
            system_prompt=self._system_prompt,
            user_message=user_message,
            output_model=ResearchReport,
        )
        report.tool_calls_made = 0
        logger.info("Research complete — %d tickers analysed", len(report.tickers))
        return report

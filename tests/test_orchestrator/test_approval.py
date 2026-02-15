import time
from datetime import date

import pytest

from src.db.models import DailyPicks, LLMProvider, StockPick
from src.orchestrator.approval import CLIApprovalFlow


def _sample_picks() -> DailyPicks:
    return DailyPicks(
        llm=LLMProvider.CLAUDE,
        pick_date=date(2026, 2, 16),
        picks=[
            StockPick(ticker="ASML.AS", allocation_pct=60.0),
            StockPick(ticker="SAP.DE", allocation_pct=40.0),
        ],
        confidence=0.7,
    )


class TestCLIApprovalFlow:
    @pytest.mark.asyncio
    async def test_request_approve_all(self):
        flow = CLIApprovalFlow(input_func=lambda _: "a")
        decision = await flow.request(_sample_picks())
        assert decision.action == "approve_all"
        assert decision.approved_indices == [0, 1]

    @pytest.mark.asyncio
    async def test_request_reject_all(self):
        flow = CLIApprovalFlow(input_func=lambda _: "r")
        decision = await flow.request(_sample_picks())
        assert decision.action == "reject_all"
        assert decision.approved_indices == []

    @pytest.mark.asyncio
    async def test_request_subset(self):
        flow = CLIApprovalFlow(input_func=lambda _: "2")
        decision = await flow.request(_sample_picks())
        assert decision.action == "approve_subset"
        assert decision.approved_indices == [1]

    @pytest.mark.asyncio
    async def test_timeout_uses_policy(self):
        def _slow_input(_: str) -> str:
            time.sleep(0.2)
            return "a"

        flow = CLIApprovalFlow(
            timeout_seconds=0,
            timeout_action="approve_all",
            input_func=_slow_input,
        )
        decision = await flow.request(_sample_picks())
        assert decision.timed_out is True
        assert decision.action == "approve_all"
        assert decision.approved_indices == [0, 1]

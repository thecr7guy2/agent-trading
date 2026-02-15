from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.db.models import LLMProvider, PnLReport
from src.reporting.leaderboard import LeaderboardBuilder


def _make_report(llm: LLMProvider, pnl: str, wins: int = 1, losses: int = 0) -> PnLReport:
    total_trades = wins + losses
    return PnLReport(
        llm_name=llm,
        period_start=date(2026, 2, 10),
        period_end=date(2026, 2, 14),
        total_invested=Decimal("50"),
        total_value=Decimal("50") + Decimal(pnl),
        realized_pnl=Decimal(pnl),
        unrealized_pnl=Decimal("0"),
        total_pnl=Decimal(pnl),
        return_pct=float(Decimal(pnl) / 50 * 100),
        win_count=wins,
        loss_count=losses,
        win_rate=round(wins / total_trades, 2) if total_trades else 0.0,
        is_real=True,
    )


@pytest.mark.asyncio
async def test_build_ranks_by_pnl():
    engine = AsyncMock()
    # Claude: real +4, virtual +1 => total +5
    # MiniMax: real +1, virtual +0 => total +1
    engine.get_pnl_report.side_effect = [
        _make_report(LLMProvider.CLAUDE, "4"),
        _make_report(LLMProvider.CLAUDE, "1"),
        _make_report(LLMProvider.MINIMAX, "1"),
        _make_report(LLMProvider.MINIMAX, "0"),
    ]

    builder = LeaderboardBuilder(engine)
    result = await builder.build(date(2026, 2, 10), date(2026, 2, 14))

    assert len(result) == 2
    assert result[0]["llm_name"] == "claude"
    assert result[0]["rank"] == 1
    assert float(result[0]["pnl"]) > float(result[1]["pnl"])
    assert result[1]["llm_name"] == "minimax"
    assert result[1]["rank"] == 2


@pytest.mark.asyncio
async def test_build_empty():
    engine = AsyncMock()
    zero = _make_report(LLMProvider.CLAUDE, "0", wins=0, losses=0)
    engine.get_pnl_report.return_value = zero

    builder = LeaderboardBuilder(engine)
    result = await builder.build(date(2026, 2, 10), date(2026, 2, 14))

    assert len(result) == 2
    for entry in result:
        assert float(entry["pnl"]) == 0.0
        assert entry["total_trades"] == 0

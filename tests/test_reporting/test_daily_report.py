from datetime import date

import pytest

from src.reporting.daily_report import generate_daily_report, write_daily_report


def _decision_result():
    return {
        "status": "ok",
        "date": "2026-02-16",
        "main_trader": "claude",
        "virtual_trader": "minimax",
        "approval": {"action": "approve_all", "approved_indices": [0], "timed_out": False},
        "reddit_posts": 42,
        "tickers_analyzed": 5,
        "real_execution": [
            {"ticker": "ASML.AS", "action": "buy", "status": "filled"},
        ],
        "virtual_execution": [
            {"ticker": "SAP.DE", "action": "buy", "status": "filled"},
        ],
    }


def _eod_result():
    return {
        "status": "ok",
        "date": "2026-02-16",
        "snapshots": {
            "claude_real": {
                "total_invested": "25.00",
                "total_value": "26.50",
                "realized_pnl": "0",
                "unrealized_pnl": "1.50",
            },
            "minimax_virtual": {
                "total_invested": "25.00",
                "total_value": "24.80",
                "realized_pnl": "0",
                "unrealized_pnl": "-0.20",
            },
        },
    }


@pytest.mark.asyncio
async def test_generate_daily_report_markdown():
    content = await generate_daily_report(
        run_date=date(2026, 2, 16),
        decision_result=_decision_result(),
        eod_result=_eod_result(),
    )

    assert "# Daily Trading Report" in content
    assert "2026-02-16" in content
    assert "Claude" in content
    assert "Minimax" in content
    assert "ASML.AS" in content
    assert "SAP.DE" in content
    assert "## Portfolio Snapshot" in content
    assert "approve_all" in content


@pytest.mark.asyncio
async def test_generate_daily_report_no_trades():
    decision = _decision_result()
    decision["real_execution"] = []
    decision["virtual_execution"] = []

    content = await generate_daily_report(
        run_date=date(2026, 2, 16),
        decision_result=decision,
        eod_result=_eod_result(),
    )

    assert "no trades" in content


def test_write_daily_report_creates_file(tmp_path):
    content = "# Test Report\nSome content"
    path = write_daily_report(content, date(2026, 2, 16), reports_dir=str(tmp_path / "reports"))

    assert path.exists()
    assert path.name == "2026-02-16.md"
    assert path.read_text() == content


def test_write_daily_report_creates_dir(tmp_path):
    target = tmp_path / "nested" / "reports"
    assert not target.exists()

    write_daily_report("test", date(2026, 2, 16), reports_dir=str(target))
    assert target.exists()
    assert (target / "2026-02-16.md").exists()

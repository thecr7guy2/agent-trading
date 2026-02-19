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
        "pipeline_analysis": {
            "claude": {
                "picks": [
                    {
                        "ticker": "ASML.AS",
                        "action": "buy",
                        "allocation_pct": 60.0,
                        "reasoning": "Strong fundamentals, bullish technicals, positive sentiment.",
                    }
                ],
                "confidence": 0.8,
                "market_summary": "EU markets rallying on strong earnings.",
                "researched_tickers": [
                    {
                        "ticker": "ASML.AS",
                        "fundamental_score": 8.5,
                        "technical_score": 7.0,
                        "risk_score": 3.0,
                        "summary": "Semiconductor leader with strong order book.",
                        "catalyst": "Earnings beat expectations",
                        "news_summary": "Q4 earnings above consensus",
                    },
                    {
                        "ticker": "ING.AS",
                        "fundamental_score": 5.0,
                        "technical_score": 4.0,
                        "risk_score": 6.0,
                        "summary": "Banking sector under pressure.",
                        "catalyst": "",
                        "news_summary": "",
                    },
                ],
                "not_picked": [
                    {
                        "ticker": "ING.AS",
                        "fundamental_score": 5.0,
                        "technical_score": 4.0,
                        "risk_score": 6.0,
                        "summary": "Banking sector under pressure.",
                        "catalyst": "",
                        "news_summary": "",
                    },
                ],
                "risk_review": {
                    "risk_notes": "Portfolio well diversified.",
                    "adjustments": ["Reduced ASML.AS from 70% to 60%"],
                    "vetoed_tickers": [],
                },
            },
        },
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
    assert "ASML.AS" in content
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


@pytest.mark.asyncio
async def test_report_includes_pick_reasoning():
    content = await generate_daily_report(
        run_date=date(2026, 2, 16),
        decision_result=_decision_result(),
        eod_result=_eod_result(),
    )

    # Pick reasoning
    assert "Strong fundamentals, bullish technicals" in content
    assert "60%" in content

    # Research scores
    assert "### Research Scores" in content
    assert "8.5" in content  # ASML fundamental score
    assert "Semiconductor leader" in content

    # Not picked section
    assert "### Not Picked" in content
    assert "ING.AS" in content
    assert "weak technicals" in content

    # Risk review
    assert "### Risk Review" in content
    assert "Portfolio well diversified" in content
    assert "Reduced ASML.AS from 70% to 60%" in content

    # Market summary
    assert "EU markets rallying" in content

    # Confidence
    assert "80%" in content


@pytest.mark.asyncio
async def test_report_without_pipeline_analysis():
    decision = _decision_result()
    del decision["pipeline_analysis"]

    content = await generate_daily_report(
        run_date=date(2026, 2, 16),
        decision_result=decision,
        eod_result=_eod_result(),
    )

    # Should still render execution tables without analysis
    assert "## Execution" in content
    assert "ASML.AS" in content


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

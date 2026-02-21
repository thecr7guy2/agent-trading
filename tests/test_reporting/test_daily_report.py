from datetime import date
from unittest.mock import patch

import pytest

from src.reporting.daily_report import generate_daily_report, write_daily_report


def _decision_result():
    return {
        "status": "ok",
        "date": "2026-02-16",
        "conservative_trader": "claude",
        "aggressive_trader": "claude_aggressive",
        "reddit_posts": 42,
        "tickers_analyzed": 5,
        "blacklisted_candidates": ["NVDA"],
        "signal_digest": {
            "candidates": [
                {
                    "ticker": "ASML.AS",
                    "sources": ["screener", "reddit"],
                    "screener": {"name": "ASML Holding NV"},
                },
                {
                    "ticker": "SAP.DE",
                    "sources": ["insider"],
                    "insider": {"company": "SAP SE"},
                },
            ]
        },
        "real_execution": [
            {
                "ticker": "ASML.AS",
                "status": "filled",
                "amount_eur": 5.00,
                "quantity": 0.028,
                "broker_ticker": "ASML_AS_EQ",
            },
        ],
        "practice_execution": [
            {
                "ticker": "SAP.DE",
                "status": "filled",
                "amount_eur": 250.0,
                "quantity": 1.38,
                "broker_ticker": "SAP_DE_EQ",
            },
            {
                "ticker": "RWE.DE",
                "status": "failed",
                "error": "not tradable on Trading 212",
            },
        ],
        "pipeline_analysis": {
            "conservative": {
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
                "researched_tickers": [],
                "not_picked": [
                    {
                        "ticker": "ING.AS",
                        "fundamental_score": 5.0,
                        "technical_score": 4.0,
                        "risk_score": 6.0,
                        "summary": "Banking sector under pressure.",
                    },
                ],
                "risk_review": {},
            },
        },
    }


def _eod_result():
    return {
        "status": "ok",
        "date": "2026-02-16",
        "snapshots": {
            "conservative_real": {
                "total_invested": "25.00",
                "total_value": "26.50",
                "unrealized_pnl": "1.50",
            },
        },
        "live_positions": [
            {
                "ticker": "ASML.AS",
                "quantity": 0.14,
                "avg_buy_price": 178.50,
                "current_price": 181.20,
                "open_date": "2026-02-15",
            }
        ],
        "demo_positions": [],
    }


def _mock_settings():
    from types import SimpleNamespace
    return SimpleNamespace(
        daily_budget_eur=10.0,
        practice_daily_budget_eur=500.0,
        recently_traded_days=3,
    )


@pytest.mark.asyncio
async def test_generate_daily_report_markdown():
    with patch("src.reporting.daily_report.get_settings", return_value=_mock_settings()):
        content = await generate_daily_report(
            run_date=date(2026, 2, 16),
            decision_result=_decision_result(),
            eod_result=_eod_result(),
        )

    assert "# Trading Report — 2026-02-16" in content
    assert "2026-02-16" in content
    assert "ASML.AS" in content
    assert "## Summary" in content
    assert "## Today's Buys" in content
    assert "## Current Positions" in content


@pytest.mark.asyncio
async def test_generate_daily_report_no_trades():
    decision = _decision_result()
    decision["real_execution"] = []
    decision["practice_execution"] = []

    with patch("src.reporting.daily_report.get_settings", return_value=_mock_settings()):
        content = await generate_daily_report(
            run_date=date(2026, 2, 16),
            decision_result=decision,
            eod_result=_eod_result(),
        )

    assert "No positions taken today" in content


@pytest.mark.asyncio
async def test_report_includes_buy_details():
    with patch("src.reporting.daily_report.get_settings", return_value=_mock_settings()):
        content = await generate_daily_report(
            run_date=date(2026, 2, 16),
            decision_result=_decision_result(),
            eod_result=_eod_result(),
        )

    # Buy table should contain ticker, company, signal source, reasoning
    assert "ASML.AS" in content
    assert "ASML Holding NV" in content
    assert "Screener" in content
    assert "Strong fundamentals, bullish technicals" in content

    # Practice buy
    assert "SAP.DE" in content
    assert "SAP SE" in content
    assert "Insider buy" in content


@pytest.mark.asyncio
async def test_report_skipped_section():
    with patch("src.reporting.daily_report.get_settings", return_value=_mock_settings()):
        content = await generate_daily_report(
            run_date=date(2026, 2, 16),
            decision_result=_decision_result(),
            eod_result=_eod_result(),
        )

    assert "## Skipped / Failed" in content
    # Blacklisted ticker
    assert "NVDA" in content
    assert "Blacklisted" in content
    # Failed order
    assert "RWE.DE" in content
    assert "not tradable on Trading 212" in content
    # Not-picked ticker from research
    assert "ING.AS" in content


@pytest.mark.asyncio
async def test_report_positions_table():
    with patch("src.reporting.daily_report.get_settings", return_value=_mock_settings()):
        content = await generate_daily_report(
            run_date=date(2026, 2, 16),
            decision_result=_decision_result(),
            eod_result=_eod_result(),
        )

    assert "## Current Positions" in content
    assert "### Real Account" in content
    assert "ASML.AS" in content
    # Should show avg buy price and current price
    assert "178.50" in content
    assert "181.20" in content


@pytest.mark.asyncio
async def test_report_with_sell_results():
    with patch("src.reporting.daily_report.get_settings", return_value=_mock_settings()):
        content = await generate_daily_report(
            run_date=date(2026, 2, 16),
            decision_result=_decision_result(),
            eod_result=_eod_result(),
            sell_results=[
                {
                    "ticker": "SAP.DE",
                    "signal_type": "take_profit",
                    "return_pct": 15.3,
                    "reasoning": "Take-profit: +15.3% (threshold: +15.0%)",
                }
            ],
        )

    assert "## Sell Triggers" in content
    assert "SAP.DE" in content
    assert "take_profit" in content
    assert "+15.3%" in content


@pytest.mark.asyncio
async def test_report_without_pipeline_analysis():
    decision = _decision_result()
    del decision["pipeline_analysis"]

    with patch("src.reporting.daily_report.get_settings", return_value=_mock_settings()):
        content = await generate_daily_report(
            run_date=date(2026, 2, 16),
            decision_result=decision,
            eod_result=_eod_result(),
        )

    # Should still render buy table without analysis
    assert "## Today's Buys" in content
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

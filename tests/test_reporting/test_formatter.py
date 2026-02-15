from src.reporting.formatter import (
    format_best_worst,
    format_full_report,
    format_leaderboard,
    format_portfolio_summary,
)


def _sample_leaderboard():
    return [
        {
            "rank": 1,
            "llm_name": "claude",
            "pnl": "4.32",
            "win_rate": 0.667,
            "avg_return": 2.1,
            "total_trades": 6,
        },
        {
            "rank": 2,
            "llm_name": "minimax",
            "pnl": "1.15",
            "win_rate": 0.5,
            "avg_return": 0.8,
            "total_trades": 4,
        },
    ]


def _sample_summary():
    return {
        "total_invested": "50.00",
        "total_value": "51.87",
        "pnl": "1.87",
        "return_pct": 3.7,
    }


def _sample_best_worst():
    return {
        "best": {"ticker": "ASML.AS", "return_pct": 8.2, "llm": "claude", "date": "2026-02-10"},
        "worst": {"ticker": "SAP.DE", "return_pct": -4.1, "llm": "minimax", "date": "2026-02-11"},
    }


def test_format_leaderboard_contains_data():
    output = format_leaderboard(_sample_leaderboard())
    assert "claude" in output
    assert "minimax" in output
    assert "4.32" in output


def test_format_portfolio_summary_contains_values():
    output = format_portfolio_summary(_sample_summary())
    assert "50.00" in output
    assert "51.87" in output


def test_format_best_worst_contains_tickers():
    output = format_best_worst(_sample_best_worst())
    assert "ASML.AS" in output
    assert "SAP.DE" in output


def test_format_full_report_has_all_sections():
    lb = format_leaderboard(_sample_leaderboard())
    ps = format_portfolio_summary(_sample_summary())
    bw = format_best_worst(_sample_best_worst())
    output = format_full_report("WEEKLY REPORT (Feb 10-14, 2026)", lb, ps, bw)

    assert "WEEKLY REPORT" in output
    assert "claude" in output
    assert "ASML.AS" in output
    assert "50.00" in output

import json

from src.reporting import dashboard


def test_refresh_portfolio_snapshot_uses_account_cash_totals(tmp_path, monkeypatch):
    data_file = tmp_path / "data.json"
    monkeypatch.setattr(dashboard, "_DATA_FILE", data_file)
    monkeypatch.setattr(dashboard, "_compute_sp100_history", lambda data: [])

    demo_positions = [
        {
            "ticker": "SCVL_US_EQ",
            "quantity": 10.0,
            "avg_buy_price": 20.0,
            "current_price": 22.0,
            "open_date": "2026-04-07",
        }
    ]
    account_cash = {"invested": 30_918.8, "ppl": -42.23}

    dashboard.refresh_portfolio_snapshot(demo_positions, account_cash=account_cash)

    data = json.loads(data_file.read_text())
    portfolio = data["portfolio"]
    assert portfolio["total_invested_eur"] == 30918.8
    assert portfolio["unrealized_pnl_eur"] == -42.23
    assert portfolio["total_value_eur"] == 30876.57
    assert len(portfolio["positions"]) == 1

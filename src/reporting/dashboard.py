import json
import logging
import re
import subprocess
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DATA_FILE = _PROJECT_ROOT / "docs" / "data.json"


def _read_data() -> dict:
    if _DATA_FILE.exists():
        try:
            return json.loads(_DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Could not parse existing data.json — starting fresh")
    return {
        "last_updated": None,
        "portfolio": {
            "total_invested_eur": 0,
            "total_value_eur": 0,
            "unrealized_pnl_eur": 0,
            "unrealized_pnl_pct": 0,
            "positions": [],
        },
        "runs": [],
        "portfolio_history": [],
    }


def _days_held(pos: dict) -> int:
    raw = pos.get("open_date") or pos.get("opened_at", "")
    if not raw:
        return 0
    match = re.match(r"(\d{4}-\d{2}-\d{2})", str(raw))
    if not match:
        return 0
    try:
        return (date.today() - date.fromisoformat(match.group(1))).days
    except ValueError:
        return 0


def _build_positions(demo_positions: list[dict]) -> list[dict]:
    positions = []
    for pos in demo_positions:
        ticker = pos.get("ticker", "")
        if not ticker:
            continue
        try:
            qty = float(pos.get("quantity", 0) or 0)
            avg = float(pos.get("avg_buy_price", 0) or 0)
            current = float(pos.get("current_price", 0) or 0)
        except (TypeError, ValueError):
            qty, avg, current = 0.0, 0.0, 0.0

        invested = round(qty * avg, 2)
        value = round(qty * current, 2) if current > 0 else invested
        pnl_eur = round(value - invested, 2)
        pnl_pct = round((pnl_eur / invested * 100) if invested > 0 else 0.0, 2)

        positions.append(
            {
                "ticker": ticker,
                "quantity": round(qty, 4),
                "avg_buy_price": round(avg, 4),
                "current_price": round(current, 4),
                "invested_eur": invested,
                "current_value_eur": value,
                "pnl_eur": pnl_eur,
                "pnl_pct": pnl_pct,
                "days_held": _days_held(pos),
            }
        )
    return positions


def _build_run_entry(decision_result: dict, run_number: int) -> dict:
    execution = decision_result.get("execution", [])
    picks_raw = decision_result.get("picks", [])

    # Build lookup: ticker → execution details (amount_eur, quantity)
    exec_map: dict[str, dict] = {
        e["ticker"]: e for e in execution if e.get("ticker") and e.get("status") == "filled"
    }

    total_spent = sum(
        float(e.get("amount_eur", 0) or 0) for e in execution if e.get("status") == "filled"
    )

    picks_out = []
    for p in picks_raw:
        ticker = p.get("ticker", "")
        executed = ticker in exec_map
        exec_info = exec_map.get(ticker, {})
        picks_out.append(
            {
                "ticker": ticker,
                "allocation_pct": p.get("allocation_pct", 0.0),
                "reasoning": (p.get("reasoning") or "")[:200],
                "executed": executed,
                "amount_eur": round(float(exec_info.get("amount_eur", 0) or 0), 2),
                "quantity": round(float(exec_info.get("quantity", 0) or 0), 4),
            }
        )

    confidence_raw = decision_result.get("confidence", 0.0)
    confidence_pct = (
        round(float(confidence_raw or 0) * 100)
        if float(confidence_raw or 0) <= 1.0
        else round(float(confidence_raw or 0))
    )

    return {
        "date": decision_result.get("date", str(date.today())),
        "run_number": run_number,
        "insider_count": decision_result.get("insider_count", 0),
        "confidence": confidence_pct,
        "market_summary": (decision_result.get("market_summary") or "")[:300],
        "total_spent_eur": round(total_spent, 2),
        "picks": picks_out,
    }


def update_dashboard_data(decision_result: dict, eod_result: dict) -> None:
    data = _read_data()

    # --- Portfolio snapshot from EOD ---
    snapshot = eod_result.get("snapshots", {}).get("demo", {})
    total_invested = round(float(snapshot.get("total_invested", 0) or 0), 2)
    total_value = round(float(snapshot.get("total_value", 0) or 0), 2)
    pnl_eur = round(float(snapshot.get("unrealized_pnl", 0) or 0), 2)
    pnl_pct = round((pnl_eur / total_invested * 100) if total_invested > 0 else 0.0, 2)

    positions = _build_positions(eod_result.get("demo_positions", []))

    data["portfolio"] = {
        "total_invested_eur": total_invested,
        "total_value_eur": total_value,
        "unrealized_pnl_eur": pnl_eur,
        "unrealized_pnl_pct": pnl_pct,
        "positions": positions,
    }

    # --- Append run entry (only if decision actually ran) ---
    if decision_result.get("status") == "ok":
        run_number = len(data.get("runs", [])) + 1
        run_entry = _build_run_entry(decision_result, run_number)
        data.setdefault("runs", [])
        data["runs"].append(run_entry)

    # --- Append portfolio history point ---
    run_date = eod_result.get("date", str(date.today()))
    history: list[dict] = data.get("portfolio_history", [])
    # Replace entry for same date if it exists, otherwise append
    history = [h for h in history if h.get("date") != run_date]
    history.append(
        {
            "date": run_date,
            "total_invested_eur": total_invested,
            "total_value_eur": total_value,
            "unrealized_pnl_eur": pnl_eur,
        }
    )
    # Keep last 70 data points (~10 weeks at 2x/week + buffer)
    data["portfolio_history"] = history[-70:]

    data["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

    _DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Dashboard data updated → %s", _DATA_FILE)


def push_dashboard_data() -> None:
    root = str(_PROJECT_ROOT)
    today = date.today().isoformat()
    cmds = [
        ["git", "-C", root, "add", "docs/data.json"],
        ["git", "-C", root, "commit", "-m", f"chore: update dashboard {today}"],
        ["git", "-C", root, "push", "origin", "master"],
    ]
    for cmd in cmds:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                # "nothing to commit" is not a real error
                if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
                    logger.info("Dashboard data unchanged — no new commit needed")
                    return
                logger.warning(
                    "git command failed: %s\nstdout: %s\nstderr: %s",
                    cmd,
                    result.stdout,
                    result.stderr,
                )
                return
            logger.info("git %s OK", cmd[3] if len(cmd) > 3 else cmd)
        except subprocess.TimeoutExpired:
            logger.warning("git command timed out: %s", cmd)
            return
        except Exception:
            logger.exception("Unexpected error running git command: %s", cmd)
            return
    logger.info("Dashboard data pushed to origin/master")

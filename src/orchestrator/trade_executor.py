"""
Trade executor with fallback logic.

Tries to spend the full budget across a ranked candidate list.
If a buy order fails for any reason (ticker not on T212, insufficient funds,
order rejected), it skips to the next candidate and tries again.
Continues until the budget is spent or all candidates are exhausted.
"""

import logging
from dataclasses import dataclass, field

from src.config import get_settings
from src.mcp_servers.trading.t212_client import T212Client, T212Error
from src.utils.recently_traded import add_many

logger = logging.getLogger(__name__)


@dataclass
class TradeResult:
    ticker: str
    success: bool
    amount_spent: float = 0.0
    quantity: float = 0.0
    broker_ticker: str = ""
    error: str = ""
    order: dict = field(default_factory=dict)


@dataclass
class ExecutionSummary:
    is_real: bool
    budget: float
    available_cash: float
    total_spent: float
    bought: list[TradeResult] = field(default_factory=list)
    failed: list[TradeResult] = field(default_factory=list)

    @property
    def num_bought(self) -> int:
        return len(self.bought)

    @property
    def budget_utilisation_pct(self) -> float:
        if self.budget <= 0:
            return 0.0
        return round(self.total_spent / self.budget * 100, 1)


async def execute_with_fallback(
    candidates: list[dict],
    is_real: bool,
    t212: T212Client,
) -> ExecutionSummary:
    """
    Try to fully spend the configured daily budget across the candidate list.

    Args:
        candidates: Ranked list of dicts with at least 'ticker' and 'price' keys.
                    Claude's top pick should be first. Each must have a valid price.
        is_real:    True = live account (real money), False = demo (practice).
        t212:       Pre-initialised T212Client for the correct account.

    Returns:
        ExecutionSummary with bought/failed lists and spend stats.
    """
    settings = get_settings()
    budget = settings.daily_budget_eur if is_real else settings.practice_daily_budget_eur

    # Get actual available cash — cap budget to what's actually there
    available_cash = budget
    try:
        cash_info = await t212.get_account_cash()
        available_cash = float(cash_info.get("free", cash_info.get("freeForStocks", budget)))
        effective_budget = min(budget, available_cash)
        logger.info(
            "[%s] Budget: €%.2f | Cash available: €%.2f | Effective: €%.2f",
            "REAL" if is_real else "DEMO",
            budget,
            available_cash,
            effective_budget,
        )
    except Exception:
        effective_budget = budget
        logger.warning("Could not fetch cash balance — using configured budget: €%.2f", budget)

    summary = ExecutionSummary(
        is_real=is_real,
        budget=budget,
        available_cash=available_cash,
        total_spent=0.0,
    )

    for candidate in candidates:
        remaining = effective_budget - summary.total_spent

        if remaining < 1.0:
            logger.info("Budget fully allocated (€%.2f spent)", summary.total_spent)
            break

        ticker = (candidate.get("ticker") or "").strip().upper()
        price = candidate.get("price") or candidate.get("current_price")

        if not ticker:
            continue

        if not price or float(price) <= 0:
            summary.failed.append(TradeResult(
                ticker=ticker,
                success=False,
                error="no valid price — skipping",
            ))
            logger.warning("No valid price for %s — skipping", ticker)
            continue

        result = await _try_buy(t212, ticker, remaining, float(price))

        if result.success:
            summary.total_spent += result.amount_spent
            summary.bought.append(result)
            logger.info(
                "✓ Bought %s — €%.2f spent | total: €%.2f / €%.2f",
                ticker, result.amount_spent, summary.total_spent, effective_budget,
            )
        else:
            summary.failed.append(result)
            logger.warning("✗ Skipped %s — %s", ticker, result.error)

    # Blacklist everything that was successfully bought
    if summary.bought:
        settings = get_settings()
        add_many(
            [r.ticker for r in summary.bought],
            path=settings.recently_traded_path,
        )
        logger.info(
            "Blacklisted %d tickers for %d days: %s",
            len(summary.bought),
            settings.recently_traded_days,
            [r.ticker for r in summary.bought],
        )

    return summary


async def _try_buy(
    t212: T212Client,
    ticker: str,
    amount_eur: float,
    current_price: float,
) -> TradeResult:
    """Attempt a single buy. Returns TradeResult regardless of outcome."""
    try:
        broker_ticker = await t212.resolve_ticker(ticker)
        if not broker_ticker:
            return TradeResult(
                ticker=ticker,
                success=False,
                error="not tradable on Trading 212",
            )

        quantity = amount_eur / current_price
        order = await t212.place_market_order(broker_ticker, quantity)

        filled_qty = float(order.get("filledQuantity", quantity))
        filled_value = float(order.get("filledValue", amount_eur))

        return TradeResult(
            ticker=ticker,
            success=True,
            amount_spent=filled_value,
            quantity=filled_qty,
            broker_ticker=broker_ticker,
            order=order,
        )

    except T212Error as e:
        return TradeResult(
            ticker=ticker,
            success=False,
            error=f"T212 error {e.status_code}: {e.message}",
        )
    except ValueError as e:
        # e.g. quantity rounds to 0
        return TradeResult(ticker=ticker, success=False, error=str(e))
    except Exception as e:
        return TradeResult(ticker=ticker, success=False, error=str(e))

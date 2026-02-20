import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date

from src.agents.pipeline import AgentPipeline
from src.backtesting.data_source import BacktestDataSource
from src.config import Settings, get_settings
from src.db.connection import get_pool
from src.db.models import DailyPicks, LLMProvider, PickReview
from src.orchestrator.mcp_client import create_market_data_client
from src.orchestrator.rotation import is_trading_day
from src.orchestrator.sell_strategy import SellStrategyEngine
from src.orchestrator.supervisor import Supervisor

logger = logging.getLogger(__name__)


@dataclass
class SimulatedPosition:
    ticker: str
    quantity: float
    avg_buy_price: float
    opened_date: date


@dataclass
class SimulatedPortfolio:
    positions: dict[str, SimulatedPosition] = field(default_factory=dict)
    realized_pnl: float = 0.0
    total_invested: float = 0.0
    trades: list[dict] = field(default_factory=list)

    def buy(self, ticker: str, amount: float, price: float, trade_date: date) -> None:
        if price <= 0:
            return
        qty = amount / price
        if ticker in self.positions:
            pos = self.positions[ticker]
            total_qty = pos.quantity + qty
            pos.avg_buy_price = (pos.quantity * pos.avg_buy_price + qty * price) / total_qty
            pos.quantity = total_qty
        else:
            self.positions[ticker] = SimulatedPosition(
                ticker=ticker, quantity=qty, avg_buy_price=price, opened_date=trade_date
            )
        self.total_invested += amount
        self.trades.append(
            {
                "action": "buy",
                "ticker": ticker,
                "quantity": round(qty, 6),
                "price": round(price, 4),
                "amount": round(amount, 2),
                "date": str(trade_date),
            }
        )

    def sell(self, ticker: str, price: float, trade_date: date, reason: str = "") -> dict | None:
        pos = self.positions.get(ticker)
        if not pos or pos.quantity <= 0:
            return None
        proceeds = pos.quantity * price
        cost_basis = pos.quantity * pos.avg_buy_price
        pnl = proceeds - cost_basis
        self.realized_pnl += pnl

        trade = {
            "action": "sell",
            "ticker": ticker,
            "quantity": round(pos.quantity, 6),
            "price": round(price, 4),
            "proceeds": round(proceeds, 2),
            "pnl": round(pnl, 2),
            "reason": reason,
            "date": str(trade_date),
        }
        self.trades.append(trade)
        del self.positions[ticker]
        return trade

    def portfolio_value(self, prices: dict[str, float]) -> float:
        total = 0.0
        for ticker, pos in self.positions.items():
            price = prices.get(ticker, pos.avg_buy_price)
            total += pos.quantity * price
        return total


@dataclass
class BacktestResult:
    run_id: int
    name: str
    start_date: date
    end_date: date
    days_traded: int
    llm_results: dict[str, dict]


class BacktestEngine:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._sell_engine = SellStrategyEngine(self._settings)

    async def run(
        self,
        start_date: date,
        end_date: date,
        run_name: str | None = None,
        budget_eur: float | None = None,
    ) -> BacktestResult:
        budget = budget_eur or self._settings.backtest_daily_budget_eur
        run_name = run_name or f"backtest_{start_date}_{end_date}"

        pool = await get_pool()
        data_source = BacktestDataSource(pool)
        market_client = create_market_data_client()

        available_dates = await data_source.get_available_dates(start_date, end_date)
        trading_dates = [
            d for d in available_dates if is_trading_day(d, self._settings.orchestrator_timezone)
        ]

        if not trading_dates:
            logger.warning(
                "No trading dates with sentiment data in range %s to %s", start_date, end_date
            )
            run_id = await data_source.save_backtest_run(
                run_name, start_date, end_date, status="completed", notes="No data available"
            )
            return BacktestResult(
                run_id=run_id,
                name=run_name,
                start_date=start_date,
                end_date=end_date,
                days_traded=0,
                llm_results={},
            )

        run_id = await data_source.save_backtest_run(run_name, start_date, end_date)

        portfolios: dict[str, SimulatedPortfolio] = {
            "claude_real": SimulatedPortfolio(),
            "claude_aggressive_practice": SimulatedPortfolio(),
        }

        # Maps each strategy to its portfolio key and budget
        practice_budget = getattr(self._settings, "practice_daily_budget_eur", 500.0)
        strategy_config: dict[LLMProvider, tuple[str, str, float, bool]] = {
            LLMProvider.CLAUDE: ("claude_real", "conservative", budget, True),
            LLMProvider.CLAUDE_AGGRESSIVE: (
                "claude_aggressive_practice",
                "aggressive",
                practice_budget,
                False,
            ),
        }

        pipelines = {
            LLMProvider.CLAUDE: AgentPipeline(LLMProvider.CLAUDE, strategy="conservative"),
            LLMProvider.CLAUDE_AGGRESSIVE: AgentPipeline(
                LLMProvider.CLAUDE_AGGRESSIVE, strategy="aggressive"
            ),
        }

        for trade_date in trading_dates:
            logger.info("Backtesting %s...", trade_date)

            digest = await data_source.reconstruct_sentiment_digest(trade_date)
            if not digest.get("tickers"):
                logger.info("Skipping %s — no sentiment data", trade_date)
                continue

            tickers = [t["ticker"] for t in digest["tickers"] if t.get("ticker")]
            tickers = tickers[: self._settings.market_data_ticker_limit]

            prices = await self._fetch_prices(market_client, tickers)
            market_data = await self._fetch_market_data(market_client, tickers)

            # Run sell checks first
            for llm, (portfolio_key, _, _, _) in strategy_config.items():
                self._apply_sell_rules(portfolios[portfolio_key], prices, trade_date)

            # Run LLM pipelines
            for llm, (portfolio_key, strategy, llm_budget, is_real) in strategy_config.items():
                portfolio = portfolios[portfolio_key]
                portfolio_dicts = [
                    {
                        "ticker": t,
                        "quantity": str(p.quantity),
                        "avg_buy_price": str(p.avg_buy_price),
                        "is_real": is_real,
                    }
                    for t, p in portfolio.positions.items()
                ]

                try:
                    output = await pipelines[llm].run(
                        reddit_digest=digest,
                        market_data=market_data,
                        portfolio=portfolio_dicts,
                        budget_eur=llm_budget,
                        run_date=trade_date,
                    )
                    self._execute_picks(portfolio, output.picks, llm_budget, prices, trade_date)
                except Exception:
                    logger.exception("Pipeline failed for %s on %s", llm.value, trade_date)

            # Save daily results
            for llm, (portfolio_key, _, llm_budget, is_real) in strategy_config.items():
                portfolio = portfolios[portfolio_key]
                value = portfolio.portfolio_value(prices)

                await data_source.save_daily_result(
                    run_id=run_id,
                    trade_date=trade_date,
                    llm_name=llm.value,
                    is_real=is_real,
                    invested=round(portfolio.total_invested, 2),
                    value=round(value, 2),
                    realized_pnl=round(portfolio.realized_pnl, 2),
                    unrealized_pnl=round(
                        value - portfolio.total_invested + portfolio.realized_pnl, 2
                    ),
                    trades_json=[t for t in portfolio.trades if t.get("date") == str(trade_date)],
                )

        await data_source.complete_backtest_run(run_id)

        llm_results = {}
        for llm, (portfolio_key, _, _, _) in strategy_config.items():
            portfolio = portfolios[portfolio_key]
            llm_results[portfolio_key] = {
                "total_invested": round(portfolio.total_invested, 2),
                "realized_pnl": round(portfolio.realized_pnl, 2),
                "open_positions": len(portfolio.positions),
                "total_trades": len(portfolio.trades),
                "wins": sum(
                    1 for t in portfolio.trades if t.get("action") == "sell" and t.get("pnl", 0) > 0
                ),
                "losses": sum(
                    1
                    for t in portfolio.trades
                    if t.get("action") == "sell" and t.get("pnl", 0) <= 0
                ),
            }

        return BacktestResult(
            run_id=run_id,
            name=run_name,
            start_date=start_date,
            end_date=end_date,
            days_traded=len(trading_dates),
            llm_results=llm_results,
        )

    def _execute_picks(
        self,
        portfolio: SimulatedPortfolio,
        picks: PickReview | DailyPicks,
        budget: float,
        prices: dict[str, float],
        trade_date: date,
    ) -> None:
        for pick in picks.picks:
            if pick.action != "buy":
                continue
            amount = budget * (pick.allocation_pct / 100.0)
            price = prices.get(pick.ticker, 0.0)
            if amount > 0 and price > 0:
                portfolio.buy(pick.ticker, amount, price, trade_date)

        for pick in picks.sell_recommendations:
            price = prices.get(pick.ticker, 0.0)
            if price > 0:
                portfolio.sell(pick.ticker, price, trade_date, reason="llm_recommendation")

    def _apply_sell_rules(
        self,
        portfolio: SimulatedPortfolio,
        prices: dict[str, float],
        trade_date: date,
    ) -> None:
        tickers_to_sell: list[tuple[str, float, str]] = []

        for ticker, pos in portfolio.positions.items():
            price = prices.get(ticker, 0.0)
            if price <= 0:
                continue

            avg_buy = pos.avg_buy_price
            if avg_buy <= 0:
                continue

            return_pct = ((price - avg_buy) / avg_buy) * 100

            if return_pct <= -self._settings.sell_stop_loss_pct:
                tickers_to_sell.append((ticker, price, f"stop_loss ({return_pct:.1f}%)"))
            elif return_pct >= self._settings.sell_take_profit_pct:
                tickers_to_sell.append((ticker, price, f"take_profit (+{return_pct:.1f}%)"))
            else:
                days_held = (trade_date - pos.opened_date).days
                if days_held >= self._settings.sell_max_hold_days:
                    tickers_to_sell.append((ticker, price, f"hold_period ({days_held}d)"))

        for ticker, price, reason in tickers_to_sell:
            portfolio.sell(ticker, price, trade_date, reason=reason)

    async def _fetch_prices(self, market_client, tickers: list[str]) -> dict[str, float]:
        prices: dict[str, float] = {}
        for ticker in tickers:
            try:
                resp = await market_client.call_tool("get_stock_price", {"ticker": ticker})
                price = Supervisor._extract_price(resp)
                if price > 0:
                    prices[ticker] = price
            except Exception:
                logger.warning("Failed to fetch price for %s", ticker)
        return prices

    async def _fetch_market_data(self, market_client, tickers: list[str]) -> dict[str, dict]:
        result: dict[str, dict] = {}

        async def _fetch(ticker: str) -> tuple[str, dict]:
            price, fundamentals, technicals = await asyncio.gather(
                market_client.call_tool("get_stock_price", {"ticker": ticker}),
                market_client.call_tool("get_fundamentals", {"ticker": ticker}),
                market_client.call_tool("get_technical_indicators", {"ticker": ticker}),
            )
            return ticker, {"price": price, "fundamentals": fundamentals, "technicals": technicals}

        results = await asyncio.gather(*(_fetch(t) for t in tickers), return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.warning("Market data fetch failed: %s", r)
                continue
            ticker, data = r
            result[ticker] = data
        return result

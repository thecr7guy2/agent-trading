import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from src.agents.pipeline import AgentPipeline
from src.config import Settings, get_settings
from src.db.connection import get_pool
from src.db.models import DailyPicks, LLMProvider, Position, SellSignal, StockPick
from src.mcp_servers.trading.portfolio import PortfolioManager
from src.notifications.telegram import TelegramNotifier
from src.orchestrator.approval import ApprovalDecision, CLIApprovalFlow
from src.orchestrator.mcp_client import (
    MCPToolClient,
    create_market_data_client,
    create_reddit_client,
    create_trading_client,
)
from src.orchestrator.rotation import get_main_trader, get_virtual_trader, is_trading_day
from src.orchestrator.sell_strategy import SellStrategyEngine

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    llm: LLMProvider
    picks: DailyPicks
    portfolio: list[Position]


class Supervisor:
    def __init__(
        self,
        settings: Settings | None = None,
        approval_flow: CLIApprovalFlow | None = None,
        trading_client: MCPToolClient | None = None,
        reddit_client: MCPToolClient | None = None,
        market_data_client: MCPToolClient | None = None,
    ):
        self._settings = settings or get_settings()
        self._approval = approval_flow or CLIApprovalFlow(
            timeout_seconds=self._settings.approval_timeout_seconds,
            timeout_action=self._settings.approval_timeout_action,
        )
        self._pipelines: dict[LLMProvider, AgentPipeline] = {}
        self._trading_client = trading_client
        self._reddit_client = reddit_client
        self._market_data_client = market_data_client
        self._sell_engine = SellStrategyEngine(self._settings)
        self._notifier = TelegramNotifier(self._settings)

    def _ensure_clients(self) -> None:
        if self._trading_client is None:
            self._trading_client = create_trading_client()
        if self._reddit_client is None:
            self._reddit_client = create_reddit_client()
        if self._market_data_client is None:
            self._market_data_client = create_market_data_client()

    async def _get_portfolio_manager(self) -> PortfolioManager:
        pool = await get_pool()
        return PortfolioManager(pool)

    async def collect_reddit_round(self, subreddits: list[str] | None = None) -> dict:
        self._ensure_clients()
        args: dict = {}
        if subreddits is not None:
            args["subreddits"] = subreddits
        return await self._reddit_client.call_tool("collect_posts", args)

    async def build_reddit_digest(self, subreddits: list[str] | None = None) -> dict:
        self._ensure_clients()
        args: dict = {}
        if subreddits is not None:
            args["subreddits"] = subreddits
        return await self._reddit_client.call_tool("get_daily_digest", args)

    async def build_signal_digest(self, subreddits: list[str] | None = None) -> dict:
        self._ensure_clients()
        reddit_digest = await self.build_reddit_digest(subreddits)
        candidates: dict[str, dict] = {}
        screener_count = 0

        for t in reddit_digest.get("tickers", []):
            ticker = t.get("ticker", "")
            if not ticker:
                continue
            candidates[ticker] = {
                "ticker": ticker,
                "sources": ["reddit"],
                "reddit_mentions": t.get("mentions", t.get("mention_count", 0)),
                "sentiment_score": t.get("sentiment_score", 0.0),
                "top_quotes": t.get("top_quotes", []),
                "subreddits": t.get("subreddits", {}),
            }

        try:
            screener_result = await asyncio.wait_for(
                self._market_data_client.call_tool(
                    "screen_eu_markets",
                    {
                        "exchanges": self._settings.screener_exchanges,
                        "min_market_cap": self._settings.screener_min_market_cap,
                    },
                ),
                timeout=60.0,
            )
            screener_count = screener_result.get("count", 0)
            for item in screener_result.get("results", []):
                ticker = item.get("ticker", "")
                if not ticker:
                    continue
                if ticker in candidates:
                    candidates[ticker]["sources"].append("screener")
                    candidates[ticker]["screener"] = item
                else:
                    candidates[ticker] = {
                        "ticker": ticker,
                        "sources": ["screener"],
                        "reddit_mentions": 0,
                        "sentiment_score": 0.0,
                        "screener": item,
                    }
        except Exception:
            logger.exception("Screener call failed, continuing with Reddit-only")

        try:
            earnings_result = await asyncio.wait_for(
                self._market_data_client.call_tool("get_earnings_calendar", {}),
                timeout=30.0,
            )
            for event in earnings_result.get("events", []):
                ticker = event.get("ticker", "")
                if not ticker:
                    continue
                if ticker in candidates:
                    candidates[ticker]["sources"].append("earnings")
                    candidates[ticker]["earnings"] = event
                else:
                    candidates[ticker] = {
                        "ticker": ticker,
                        "sources": ["earnings"],
                        "reddit_mentions": 0,
                        "sentiment_score": 0.0,
                        "earnings": event,
                    }
        except Exception:
            logger.exception("Earnings calendar call failed, continuing without it")

        sorted_candidates = sorted(
            candidates.values(),
            key=lambda c: (len(c.get("sources", [])), c.get("reddit_mentions", 0)),
            reverse=True,
        )
        limit = getattr(self._settings, "signal_candidate_limit", 25)
        sorted_candidates = sorted_candidates[:limit]

        top_tickers = [c["ticker"] for c in sorted_candidates[:limit]]
        news_map = await self._fetch_news_batch(top_tickers)
        for candidate in sorted_candidates:
            ticker = candidate["ticker"]
            if ticker in news_map:
                candidate["news"] = news_map[ticker]

        return {
            "candidates": sorted_candidates,
            "total_posts": reddit_digest.get("total_posts", 0),
            "screener_count": screener_count,
            "source_type": "multi",
        }

    async def _fetch_news_batch(self, tickers: list[str]) -> dict[str, list[dict]]:
        self._ensure_clients()
        news_map: dict[str, list[dict]] = {}

        async def _fetch_one(ticker: str) -> tuple[str, list[dict]]:
            try:
                result = await asyncio.wait_for(
                    self._market_data_client.call_tool("get_news", {"ticker": ticker}),
                    timeout=10.0,
                )
                return (ticker, result.get("news", []))
            except Exception:
                return (ticker, [])

        results = await asyncio.gather(*(_fetch_one(t) for t in tickers), return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                continue
            ticker, news = result
            if news:
                news_map[ticker] = news
        return news_map

    async def build_market_data(self, digest: dict) -> dict[str, dict]:
        self._ensure_clients()
        if "candidates" in digest:
            tickers = [c.get("ticker", "") for c in digest["candidates"] if c.get("ticker")]
            limit = getattr(self._settings, "signal_candidate_limit", 25)
            tickers = tickers[:limit]
        else:
            tickers = [t.get("ticker", "") for t in digest.get("tickers", []) if t.get("ticker")]
            tickers = tickers[: self._settings.market_data_ticker_limit]
        if not tickers:
            return {}

        async def _fetch(ticker: str) -> tuple[str, dict]:
            try:
                price, fundamentals, technicals = await asyncio.wait_for(
                    asyncio.gather(
                        self._market_data_client.call_tool("get_stock_price", {"ticker": ticker}),
                        self._market_data_client.call_tool("get_fundamentals", {"ticker": ticker}),
                        self._market_data_client.call_tool(
                            "get_technical_indicators", {"ticker": ticker}
                        ),
                    ),
                    timeout=30.0,
                )
            except TimeoutError:
                logger.warning("Market data fetch timed out for %s", ticker)
                return (ticker, {"price": {}, "fundamentals": {}, "technicals": {}})
            return (
                ticker,
                {
                    "price": price,
                    "fundamentals": fundamentals,
                    "technicals": technicals,
                },
            )

        results = await asyncio.gather(
            *(_fetch(ticker) for ticker in tickers), return_exceptions=True
        )
        market_data: dict[str, dict] = {}
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Market data fetch failed: %s", result)
                continue
            ticker, payload = result
            market_data[ticker] = payload
        return market_data

    async def run_decision_cycle(
        self,
        run_date: date | None = None,
        require_approval: bool = True,
        force: bool = False,
        collect_rounds: int = 0,
    ) -> dict:
        run_date = run_date or datetime.now(ZoneInfo(self._settings.orchestrator_timezone)).date()
        if not is_trading_day(run_date, self._settings.orchestrator_timezone):
            return {"status": "skipped", "reason": "non-trading-day", "date": str(run_date)}

        if collect_rounds > 0:
            await self.collect_reddit_round()

        digest = await self.build_signal_digest()
        if digest.get("error"):
            return {"status": "error", "stage": "signal_digest", "error": digest["error"]}

        market_data = await self.build_market_data(digest)
        main_trader = get_main_trader(run_date)
        virtual_trader = get_virtual_trader(run_date)

        pipeline_results = await self._run_pipelines(digest, market_data, run_date)
        result_by_llm = {item.llm: item for item in pipeline_results}

        if main_trader not in result_by_llm:
            return {"status": "error", "stage": "pipeline", "error": "main trader pipeline failed"}

        main_result = result_by_llm[main_trader]
        virtual_result = result_by_llm.get(virtual_trader)

        pm = await self._get_portfolio_manager()
        await pm.save_daily_picks(main_result.picks, is_main=True)
        if virtual_result is not None:
            await pm.save_daily_picks(virtual_result.picks, is_main=False)

        decision = await self._resolve_approval(main_result.picks, require_approval)
        approved_picks = self._select_picks(main_result.picks.picks, decision)
        approved_daily = DailyPicks(
            llm=main_result.picks.llm,
            pick_date=main_result.picks.pick_date,
            picks=approved_picks,
            sell_recommendations=main_result.picks.sell_recommendations,
            confidence=main_result.picks.confidence,
            market_summary=main_result.picks.market_summary,
        )
        self._normalize_allocations(approved_daily)

        real_execution = await self._execute_real_trades(
            llm=main_trader,
            picks=approved_daily,
            budget_eur=self._settings.daily_budget_eur,
            portfolio=main_result.portfolio,
            market_data=market_data,
            force=force,
        )
        virtual_execution = []
        if virtual_result is not None:
            virtual_execution = await self._execute_virtual_trades(
                llm=virtual_trader,
                picks=virtual_result.picks,
                budget_eur=self._settings.daily_budget_eur,
                portfolio=virtual_result.portfolio,
                market_data=market_data,
                force=force,
            )

        await self._persist_sentiment(digest, run_date)
        await self._persist_signals(digest, run_date)

        decision_result = {
            "status": "ok",
            "date": str(run_date),
            "main_trader": main_trader.value,
            "virtual_trader": virtual_trader.value,
            "approval": {
                "action": decision.action,
                "approved_indices": decision.approved_indices,
                "timed_out": decision.timed_out,
            },
            "reddit_posts": digest.get("total_posts", 0),
            "tickers_analyzed": len(market_data),
            "real_execution": real_execution,
            "virtual_execution": virtual_execution,
            "signal_digest": digest,
        }

        await self._notifier.notify_daily_summary(decision_result)
        return decision_result

    async def _run_pipelines(
        self,
        digest: dict,
        market_data: dict[str, dict],
        run_date: date,
    ) -> list[PipelineResult]:
        self._ensure_clients()
        budget = self._settings.daily_budget_eur

        async def _run_for(llm: LLMProvider) -> PipelineResult:
            pm = await self._get_portfolio_manager()
            positions = await pm.get_positions_typed(llm.value)
            portfolio_dicts = [
                {
                    "ticker": p.ticker,
                    "quantity": str(p.quantity),
                    "avg_buy_price": str(p.avg_buy_price),
                    "is_real": p.is_real,
                }
                for p in positions
            ]
            picks = await self._get_pipeline(llm).run(
                signal_digest=digest,
                market_data=market_data,
                portfolio=portfolio_dicts,
                budget_eur=budget,
                run_date=run_date,
            )
            return PipelineResult(llm=llm, picks=picks, portfolio=positions)

        tasks = [_run_for(LLMProvider.CLAUDE), _run_for(LLMProvider.MINIMAX)]
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=300.0,
            )
        except TimeoutError:
            logger.error("LLM pipelines timed out after 300s")
            return []

        successful: list[PipelineResult] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                llm_name = [LLMProvider.CLAUDE, LLMProvider.MINIMAX][i].value
                logger.exception("Pipeline failed for %s: %s", llm_name, result)
                continue
            successful.append(result)
        return successful

    def _get_pipeline(self, llm: LLMProvider) -> AgentPipeline:
        pipeline = self._pipelines.get(llm)
        if pipeline is None:
            pipeline = AgentPipeline(llm)
            self._pipelines[llm] = pipeline
        return pipeline

    async def _resolve_approval(
        self, picks: DailyPicks, require_approval: bool
    ) -> ApprovalDecision:
        if not require_approval:
            return ApprovalDecision(
                action="approve_all",
                approved_indices=list(range(len(picks.picks))),
                timed_out=False,
                raw_input="--no-approval",
            )
        return await self._approval.request(picks)

    def _select_picks(self, picks: list[StockPick], decision: ApprovalDecision) -> list[StockPick]:
        if decision.action == "reject_all":
            return []
        if decision.action == "approve_all":
            return picks
        return [picks[i] for i in decision.approved_indices if i < len(picks)]

    def _normalize_allocations(self, picks: DailyPicks) -> None:
        total = sum(max(0.0, pick.allocation_pct) for pick in picks.picks if pick.action == "buy")
        if total <= 100.0 or total == 0:
            return
        ratio = 100.0 / total
        for pick in picks.picks:
            if pick.action == "buy":
                pick.allocation_pct = round(pick.allocation_pct * ratio, 2)

    async def _execute_real_trades(
        self,
        llm: LLMProvider,
        picks: DailyPicks,
        budget_eur: float,
        portfolio: list[Position],
        market_data: dict[str, dict],
        force: bool,
    ) -> list[dict]:
        self._ensure_clients()
        executions: list[dict] = []
        pm = await self._get_portfolio_manager()

        real_positions = {p.ticker: p for p in portfolio if p.is_real}

        for pick in picks.picks:
            if pick.action != "buy":
                continue
            amount = round(budget_eur * (pick.allocation_pct / 100.0), 2)
            if amount <= 0:
                continue
            ticker_data = market_data.get(pick.ticker, {})
            price = self._extract_price(ticker_data.get("price", {}))
            if price <= 0:
                executions.append(
                    {"status": "skipped", "reason": "missing_price", "ticker": pick.ticker}
                )
                continue
            if not force and await pm.trade_exists(
                llm.value, picks.pick_date, pick.ticker, "buy", True
            ):
                executions.append(
                    {"status": "skipped", "reason": "duplicate", "ticker": pick.ticker}
                )
                continue
            result = await self._trading_client.call_tool(
                "place_buy_order",
                {
                    "llm_name": llm.value,
                    "ticker": pick.ticker,
                    "amount_eur": amount,
                    "current_price": price,
                },
            )
            executions.append(result)

        for pick in picks.sell_recommendations:
            ticker = pick.ticker
            position = real_positions.get(ticker)
            if not position:
                continue
            quantity = float(position.quantity)
            if quantity <= 0:
                continue
            if not force and await pm.trade_exists(
                llm.value, picks.pick_date, ticker, "sell", True
            ):
                executions.append({"status": "skipped", "reason": "duplicate", "ticker": ticker})
                continue
            result = await self._trading_client.call_tool(
                "place_sell_order",
                {"llm_name": llm.value, "ticker": ticker, "quantity": quantity},
            )
            executions.append(result)

        return executions

    async def _execute_virtual_trades(
        self,
        llm: LLMProvider,
        picks: DailyPicks,
        budget_eur: float,
        portfolio: list[Position],
        market_data: dict[str, dict],
        force: bool,
    ) -> list[dict]:
        self._ensure_clients()
        executions: list[dict] = []
        pm = await self._get_portfolio_manager()
        virtual_positions = {p.ticker: p for p in portfolio if not p.is_real}

        for pick in picks.picks:
            if pick.action != "buy":
                continue
            amount = budget_eur * (pick.allocation_pct / 100.0)
            if amount <= 0:
                continue
            ticker_data = market_data.get(pick.ticker, {})
            price = self._extract_price(ticker_data.get("price", {}))
            if price <= 0:
                executions.append(
                    {
                        "status": "skipped",
                        "reason": "missing_price",
                        "ticker": pick.ticker,
                    }
                )
                continue
            quantity = amount / price
            if not force and await pm.trade_exists(
                llm.value, picks.pick_date, pick.ticker, "buy", False
            ):
                executions.append(
                    {"status": "skipped", "reason": "duplicate", "ticker": pick.ticker}
                )
                continue
            result = await self._trading_client.call_tool(
                "record_virtual_trade",
                {
                    "llm_name": llm.value,
                    "ticker": pick.ticker,
                    "action": "buy",
                    "quantity": quantity,
                    "price": price,
                },
            )
            executions.append(result)

        for pick in picks.sell_recommendations:
            ticker = pick.ticker
            position = virtual_positions.get(ticker)
            if not position:
                continue
            quantity = float(position.quantity)
            if quantity <= 0:
                continue
            ticker_data = market_data.get(ticker, {})
            price = self._extract_price(ticker_data.get("price", {}))
            if price <= 0:
                price = float(position.avg_buy_price)
            if price <= 0:
                continue
            if not force and await pm.trade_exists(
                llm.value, picks.pick_date, ticker, "sell", False
            ):
                executions.append({"status": "skipped", "reason": "duplicate", "ticker": ticker})
                continue
            result = await self._trading_client.call_tool(
                "record_virtual_trade",
                {
                    "llm_name": llm.value,
                    "ticker": ticker,
                    "action": "sell",
                    "quantity": quantity,
                    "price": price,
                },
            )
            executions.append(result)

        return executions

    async def run_end_of_day(self, run_date: date | None = None) -> dict:
        self._ensure_clients()
        run_date = run_date or datetime.now(ZoneInfo(self._settings.orchestrator_timezone)).date()
        pm = await self._get_portfolio_manager()
        snapshots = {}

        for llm in LLMProvider:
            positions = await pm.get_positions_typed(llm.value)

            for is_real in (True, False):
                filtered = [p for p in positions if p.is_real == is_real]
                total_invested = Decimal("0")
                total_value = Decimal("0")

                for pos in filtered:
                    total_invested += pos.quantity * pos.avg_buy_price
                    try:
                        price_resp = await asyncio.wait_for(
                            self._market_data_client.call_tool(
                                "get_stock_price", {"ticker": pos.ticker}
                            ),
                            timeout=15.0,
                        )
                    except TimeoutError:
                        logger.warning("Price fetch timed out for %s in EOD", pos.ticker)
                        price_resp = {}
                    current_price = self._extract_price(price_resp)
                    if current_price > 0:
                        total_value += pos.quantity * Decimal(str(current_price))
                    else:
                        total_value += pos.quantity * pos.avg_buy_price

                unrealized_pnl = total_value - total_invested
                pnl_data = await pm.calculate_pnl(llm.value, run_date, run_date, is_real=is_real)
                realized_pnl = Decimal(pnl_data.get("realized_pnl", "0"))

                await pm.save_portfolio_snapshot(
                    llm_name=llm.value,
                    snapshot_date=run_date,
                    total_invested=total_invested,
                    total_value=total_value,
                    realized_pnl=realized_pnl,
                    unrealized_pnl=unrealized_pnl,
                    is_real=is_real,
                )

                label = f"{llm.value}_{'real' if is_real else 'virtual'}"
                snapshots[label] = {
                    "total_invested": str(total_invested),
                    "total_value": str(total_value),
                    "realized_pnl": str(realized_pnl),
                    "unrealized_pnl": str(unrealized_pnl),
                }

        return {"status": "ok", "date": str(run_date), "snapshots": snapshots}

    async def run_sell_checks(
        self,
        run_date: date | None = None,
        include_real: bool = True,
        include_virtual: bool = True,
    ) -> dict:
        self._ensure_clients()
        run_date = run_date or datetime.now(ZoneInfo(self._settings.orchestrator_timezone)).date()
        pm = await self._get_portfolio_manager()

        positions = await pm.get_all_positions()
        if not include_real:
            positions = [p for p in positions if not p.is_real]
        if not include_virtual:
            positions = [p for p in positions if p.is_real]

        if not positions:
            return {"status": "ok", "date": str(run_date), "executed_sells": []}

        tickers = list({p.ticker for p in positions})
        prices: dict[str, float] = {}
        for ticker in tickers:
            try:
                price_resp = await asyncio.wait_for(
                    self._market_data_client.call_tool("get_stock_price", {"ticker": ticker}),
                    timeout=15.0,
                )
            except TimeoutError:
                logger.warning("Price fetch timed out for %s in sell check", ticker)
                price_resp = {}
            prices[ticker] = self._extract_price(price_resp)

        signals = self._sell_engine.evaluate_positions(positions, prices, run_date)
        if not signals:
            return {"status": "ok", "date": str(run_date), "executed_sells": []}

        executed_sells: list[dict] = []
        for signal in signals:
            result = await self._execute_sell_signal(signal, run_date)
            executed_sells.append(result)

        sell_result = {
            "status": "ok",
            "date": str(run_date),
            "executed_sells": executed_sells,
        }
        await self._notifier.notify_sell_signals(sell_result)
        return sell_result

    async def _execute_sell_signal(self, signal: SellSignal, run_date: date) -> dict:
        self._ensure_clients()
        pm = await self._get_portfolio_manager()
        quantity = float(signal.position_qty)

        if await pm.trade_exists(
            signal.llm_name.value, run_date, signal.ticker, "sell", signal.is_real
        ):
            return {
                "status": "skipped",
                "reason": "duplicate",
                "ticker": signal.ticker,
                "signal_type": signal.signal_type,
            }

        if signal.is_real:
            result = await self._trading_client.call_tool(
                "place_sell_order",
                {"llm_name": signal.llm_name.value, "ticker": signal.ticker, "quantity": quantity},
            )
        else:
            result = await self._trading_client.call_tool(
                "record_virtual_trade",
                {
                    "llm_name": signal.llm_name.value,
                    "ticker": signal.ticker,
                    "action": "sell",
                    "quantity": quantity,
                    "price": float(signal.trigger_price),
                },
            )

        result["signal_type"] = signal.signal_type
        result["reasoning"] = signal.reasoning
        result["return_pct"] = signal.return_pct
        logger.info(
            "Sell executed: %s %s (%s, %s)",
            signal.ticker,
            signal.signal_type,
            signal.llm_name.value,
            "real" if signal.is_real else "virtual",
        )
        return result

    async def _persist_sentiment(self, digest: dict, run_date: date) -> None:
        pm = await self._get_portfolio_manager()
        if "candidates" in digest:
            items = [c for c in digest["candidates"] if "reddit" in c.get("sources", [])]
        else:
            items = digest.get("tickers", [])
        for ticker_data in items:
            ticker = ticker_data.get("ticker")
            if not ticker:
                continue
            try:
                await pm.save_sentiment_snapshot(
                    ticker=ticker,
                    scrape_date=run_date,
                    mention_count=ticker_data.get(
                        "reddit_mentions", ticker_data.get("mention_count", 0)
                    ),
                    avg_sentiment=ticker_data.get("sentiment_score", 0.0),
                    top_posts=ticker_data.get("top_quotes", []),
                    subreddits=ticker_data.get("subreddits", {}),
                )
            except Exception:
                logger.exception("Failed to persist sentiment for %s", ticker)

    async def _persist_signals(self, digest: dict, run_date: date) -> None:
        if "candidates" not in digest:
            return
        pm = await self._get_portfolio_manager()
        for candidate in digest["candidates"]:
            ticker = candidate.get("ticker")
            if not ticker:
                continue
            for source in candidate.get("sources", []):
                try:
                    evidence = {}
                    if source == "reddit":
                        evidence = {
                            "mentions": candidate.get("reddit_mentions", 0),
                            "sentiment": candidate.get("sentiment_score", 0.0),
                        }
                    elif source == "screener":
                        evidence = candidate.get("screener", {})
                    elif source == "earnings":
                        evidence = candidate.get("earnings", {})
                    await pm.save_signal_source(
                        scrape_date=run_date,
                        ticker=ticker,
                        source=source,
                        reason=source,
                        score=candidate.get("sentiment_score"),
                        evidence=evidence,
                    )
                except Exception:
                    logger.exception("Failed to persist signal source for %s/%s", ticker, source)

    @staticmethod
    def _extract_price(price_payload: dict) -> float:
        if not isinstance(price_payload, dict):
            return 0.0
        val = price_payload.get("price")
        if val is None:
            return 0.0
        try:
            return float(Decimal(str(val)))
        except (InvalidOperation, ValueError):
            return 0.0

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from src.agents.pipeline import AgentPipeline
from src.config import Settings, get_settings
from src.db.connection import get_pool
from src.db.models import (
    DailyPicks,
    LLMProvider,
    PickReview,
    Position,
    ResearchReport,
    SellSignal,
    StockPick,
)
from src.mcp_servers.trading.portfolio import PortfolioManager
from src.notifications.telegram import TelegramNotifier
from src.orchestrator.approval import ApprovalDecision, CLIApprovalFlow
from src.orchestrator.mcp_client import (
    MCPToolClient,
    create_market_data_client,
    create_reddit_client,
    create_trading_client,
)
from src.orchestrator.rotation import is_trading_day
from src.orchestrator.sell_strategy import SellStrategyEngine

logger = logging.getLogger(__name__)

# Reddit noise: common acronyms, indices, and ETFs that aren't individual stock picks
_NOISE_TICKERS = {
    # Reddit acronyms often parsed as tickers
    "FAQ",
    "DD",
    "CEO",
    "GDP",
    "IPO",
    "ATH",
    "ATL",
    "IMO",
    "YOLO",
    "FYI",
    "EPS",
    "USA",
    "USD",
    "EUR",
    "GBP",
    "ETF",
    "SEC",
    "FED",
    "CPI",
    "PPI",
    "FOMC",
    "HODL",
    "DCA",
    "OEM",
    "LLC",
    "INC",
    "YOY",
    "QOQ",
    "MOM",
    "RIP",
    "FUD",
    "APE",
    "TLDR",
}
_INDEX_TICKERS = {"VIX", "GSPC", "DJI", "IXIC", "FTSE", "DAX", "CAC"}
_COMMON_ETFS = {
    "VOO",
    "SPY",
    "QQQ",
    "SCHD",
    "VTI",
    "VEA",
    "VXUS",
    "BND",
    "VIG",
    "IWM",
    "DIA",
    "ARKK",
    "ARKW",
    "ARKG",
    "VGT",
    "SOXL",
    "SOXS",
    "TQQQ",
    "SQQQ",
    "VT",
    "QQQM",
    "JEPI",
    "JEPQ",
    "RSP",
    "XLF",
    "XLE",
    "XLK",
    "VYM",
    "VNQ",
    "GLD",
    "SLV",
    "TLT",
    "HYG",
    "LQD",
    "AGG",
    "EFA",
    "EEM",
    "IEMG",
    "SCHG",
    "QQQI",
    "SPYI",
    "VWCE",
    "NEOS",
    "IWDA",
    "VUSA",
    "CSPX",
    "VUAA",
    "VWRL",
    "SWDA",
}
_EXCLUDED = _NOISE_TICKERS | _INDEX_TICKERS | _COMMON_ETFS


def _is_valid_stock_ticker(ticker: str) -> bool:
    upper = ticker.upper()
    if upper in _EXCLUDED:
        return False
    # 1-2 char tickers from Reddit are almost always noise (II, PC, AI, EU, UK)
    if len(ticker) <= 2:
        return False
    return True


def _select_candidates(candidates: dict[str, dict], limit: int) -> list[dict]:
    # Multi-source candidates are the highest value (confirmed by 2+ independent signals)
    multi_source = sorted(
        [c for c in candidates.values() if len(c.get("sources", [])) >= 2],
        key=lambda c: (len(c["sources"]), c.get("reddit_mentions", 0)),
        reverse=True,
    )

    # Single-source buckets
    reddit_only = sorted(
        [c for c in candidates.values() if c.get("sources") == ["reddit"]],
        key=lambda c: c.get("reddit_mentions", 0),
        reverse=True,
    )
    screener_only = [
        c for c in candidates.values() if c.get("sources") == ["screener"]
    ]  # Already sorted by screener_hits from screener.py
    earnings_only = [c for c in candidates.values() if c.get("sources") == ["earnings"]]
    insider_only = sorted(
        [c for c in candidates.values() if c.get("sources") == ["insider"]],
        key=lambda c: c.get("insider", {}).get("total_value", 0),
        reverse=True,
    )

    # Build final list: multi-source first, then guaranteed slots per source
    result = list(multi_source[:limit])
    remaining = limit - len(result)
    if remaining <= 0:
        return result[:limit]

    # Reserve ~40% for EU screener, ~10% for earnings, ~10% for insider, rest for Reddit
    screener_quota = min(remaining * 2 // 5, len(screener_only))
    # Minimum 8 screener slots if available (EU stocks are the trading target)
    screener_quota = max(screener_quota, min(8, len(screener_only), remaining))
    result.extend(screener_only[:screener_quota])
    remaining -= screener_quota

    earnings_quota = min(max(remaining // 5, 1), len(earnings_only), remaining)
    result.extend(earnings_only[:earnings_quota])
    remaining -= earnings_quota

    insider_quota = min(max(remaining // 5, 1), len(insider_only), remaining)
    result.extend(insider_only[:insider_quota])
    remaining -= insider_quota

    # Fill rest with Reddit
    result.extend(reddit_only[:remaining])
    return result[:limit]


@dataclass
class PipelineResult:
    llm: LLMProvider
    picks: PickReview | DailyPicks
    research: ResearchReport | None
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
            if not ticker or not _is_valid_stock_ticker(ticker):
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

        try:
            insider_result = await asyncio.wait_for(
                self._market_data_client.call_tool(
                    "get_insider_buys",
                    {"lookback_days": getattr(self._settings, "bafin_lookback_days", 7)},
                ),
                timeout=60.0,
            )
            for trade in insider_result.get("trades", []):
                ticker = trade.get("ticker", "")
                if not ticker or not _is_valid_stock_ticker(ticker):
                    continue
                if ticker in candidates:
                    candidates[ticker]["sources"].append("insider")
                    candidates[ticker]["insider"] = trade
                else:
                    candidates[ticker] = {
                        "ticker": ticker,
                        "sources": ["insider"],
                        "reddit_mentions": 0,
                        "sentiment_score": 0.0,
                        "insider": trade,
                    }
        except Exception:
            logger.exception("BAFIN insider trades call failed, continuing without it")

        limit = getattr(self._settings, "signal_candidate_limit", 25)
        sorted_candidates = _select_candidates(candidates, limit)

        top_tickers = [c["ticker"] for c in sorted_candidates]
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

        # Run conservative (real money) and aggressive (practice) pipelines in parallel
        pipeline_results = await self._run_pipelines(digest, run_date)
        result_by_llm = {item.llm: item for item in pipeline_results}

        conservative_result = result_by_llm.get(LLMProvider.CLAUDE)
        aggressive_result = result_by_llm.get(LLMProvider.CLAUDE_AGGRESSIVE)

        if conservative_result is None:
            return {"status": "error", "stage": "pipeline", "error": "conservative pipeline failed"}

        pm = await self._get_portfolio_manager()
        conservative_daily = self._to_daily_picks(conservative_result.picks)
        await pm.save_daily_picks(conservative_daily, is_main=True)
        if aggressive_result is not None:
            aggressive_daily = self._to_daily_picks(aggressive_result.picks)
            await pm.save_daily_picks(aggressive_daily, is_main=False)

        decision = await self._resolve_approval(conservative_daily, require_approval)
        approved_picks = self._select_picks(conservative_daily.picks, decision)
        approved_daily = DailyPicks(
            llm=conservative_daily.llm,
            pick_date=conservative_daily.pick_date,
            picks=approved_picks,
            sell_recommendations=conservative_daily.sell_recommendations,
            confidence=conservative_daily.confidence,
            market_summary=conservative_daily.market_summary,
        )
        self._normalize_allocations(approved_daily)

        # Conservative → real T212 live account
        real_execution = await self._execute_real_trades(
            llm=LLMProvider.CLAUDE,
            picks=approved_daily,
            budget_eur=self._settings.daily_budget_eur,
            portfolio=conservative_result.portfolio,
            force=force,
        )

        # Aggressive → T212 practice account (virtual budget)
        practice_execution = []
        if aggressive_result is not None:
            agg_daily = self._to_daily_picks(aggressive_result.picks)
            self._normalize_allocations(agg_daily)
            practice_execution = await self._execute_practice_trades(
                llm=LLMProvider.CLAUDE_AGGRESSIVE,
                picks=agg_daily,
                budget_eur=getattr(self._settings, "practice_daily_budget_eur", 500.0),
                portfolio=aggressive_result.portfolio,
                force=force,
            )

        await self._persist_sentiment(digest, run_date)
        await self._persist_signals(digest, run_date)

        decision_result = {
            "status": "ok",
            "date": str(run_date),
            "conservative_trader": LLMProvider.CLAUDE.value,
            "aggressive_trader": LLMProvider.CLAUDE_AGGRESSIVE.value,
            "approval": {
                "action": decision.action,
                "approved_indices": decision.approved_indices,
                "timed_out": decision.timed_out,
            },
            "reddit_posts": digest.get("total_posts", 0),
            "tickers_analyzed": len(digest.get("candidates", [])),
            "real_execution": real_execution,
            "practice_execution": practice_execution,
            "signal_digest": digest,
            "pipeline_analysis": {
                "conservative": self._build_analysis_summary(conservative_result),
                **(
                    {"aggressive": self._build_analysis_summary(aggressive_result)}
                    if aggressive_result
                    else {}
                ),
            },
        }

        await self._notifier.notify_daily_summary(decision_result)
        return decision_result

    @staticmethod
    def _build_analysis_summary(result: PipelineResult) -> dict:
        picks_obj = result.picks
        picked_tickers = {p.ticker for p in picks_obj.picks}

        # Per-pick reasoning from the trader
        pick_reasoning = [
            {
                "ticker": p.ticker,
                "action": p.action,
                "allocation_pct": p.allocation_pct,
                "reasoning": p.reasoning,
            }
            for p in picks_obj.picks
        ]

        # Research summaries for all analyzed tickers (picked and not picked)
        researched: list[dict] = []
        not_picked: list[dict] = []
        if result.research and hasattr(result.research, "tickers"):
            for finding in result.research.tickers:
                entry = {
                    "ticker": finding.ticker,
                    "fundamental_score": finding.fundamental_score,
                    "technical_score": finding.technical_score,
                    "risk_score": finding.risk_score,
                    "summary": getattr(finding, "summary", ""),
                    "catalyst": getattr(finding, "catalyst", ""),
                    "news_summary": getattr(finding, "news_summary", ""),
                }
                researched.append(entry)
                if finding.ticker not in picked_tickers:
                    not_picked.append(entry)

        # Risk review details
        risk_review = {}
        if isinstance(picks_obj, PickReview):
            risk_review = {
                "risk_notes": picks_obj.risk_notes,
                "adjustments": picks_obj.adjustments,
                "vetoed_tickers": picks_obj.vetoed_tickers,
            }

        return {
            "picks": pick_reasoning,
            "confidence": picks_obj.confidence,
            "market_summary": picks_obj.market_summary,
            "researched_tickers": researched,
            "not_picked": not_picked,
            "risk_review": risk_review,
        }

    @staticmethod
    def _to_daily_picks(picks: PickReview | DailyPicks) -> DailyPicks:
        if isinstance(picks, DailyPicks):
            return picks
        return DailyPicks(
            llm=picks.llm,
            pick_date=picks.pick_date,
            picks=picks.picks,
            sell_recommendations=picks.sell_recommendations,
            confidence=picks.confidence,
            market_summary=picks.market_summary,
        )

    async def _run_pipelines(
        self,
        digest: dict,
        run_date: date,
    ) -> list[PipelineResult]:
        self._ensure_clients()
        timeout = getattr(self._settings, "pipeline_timeout_seconds", 600)

        async def _run_for(llm: LLMProvider, strategy: str, budget: float) -> PipelineResult:
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
            output = await self._get_pipeline(llm, strategy).run(
                signal_digest=digest,
                portfolio=portfolio_dicts,
                budget_eur=budget,
                run_date=run_date,
            )
            research = output.research if isinstance(output.research, ResearchReport) else None
            return PipelineResult(
                llm=llm, picks=output.picks, research=research, portfolio=positions
            )

        async def _run_with_timeout(
            llm: LLMProvider, strategy: str, budget: float
        ) -> PipelineResult | None:
            try:
                return await asyncio.wait_for(
                    _run_for(llm, strategy, budget), timeout=float(timeout)
                )
            except TimeoutError:
                logger.error("Pipeline timed out for %s after %ds", llm.value, timeout)
                return None
            except Exception:
                logger.exception("Pipeline failed for %s", llm.value)
                return None

        practice_budget = getattr(self._settings, "practice_daily_budget_eur", 500.0)
        results = await asyncio.gather(
            _run_with_timeout(LLMProvider.CLAUDE, "conservative", self._settings.daily_budget_eur),
            _run_with_timeout(LLMProvider.CLAUDE_AGGRESSIVE, "aggressive", practice_budget),
        )
        return [r for r in results if r is not None]

    def _get_pipeline(self, llm: LLMProvider, strategy: str = "conservative") -> AgentPipeline:
        key = (llm, strategy)
        pipeline = self._pipelines.get(key)  # type: ignore[arg-type]
        if pipeline is None:
            pipeline = AgentPipeline(
                llm,
                market_data_client=self._market_data_client,
                trading_client=self._trading_client,
                strategy=strategy,
            )
            self._pipelines[key] = pipeline  # type: ignore[index]
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

    async def _fetch_price(self, ticker: str) -> float:
        try:
            price_resp = await asyncio.wait_for(
                self._market_data_client.call_tool("get_stock_price", {"ticker": ticker}),
                timeout=15.0,
            )
        except TimeoutError:
            logger.warning("Price fetch timed out for %s", ticker)
            price_resp = {}
        return self._extract_price(price_resp)

    async def _execute_real_trades(
        self,
        llm: LLMProvider,
        picks: DailyPicks,
        budget_eur: float,
        portfolio: list[Position],
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
            price = await self._fetch_price(pick.ticker)
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
            price = await self._fetch_price(pick.ticker)
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
            price = await self._fetch_price(ticker)
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

    async def _execute_practice_trades(
        self,
        llm: LLMProvider,
        picks: DailyPicks,
        budget_eur: float,
        portfolio: list[Position],
        force: bool,
    ) -> list[dict]:
        """Execute aggressive strategy trades on the T212 practice (demo) account."""
        self._ensure_clients()
        executions: list[dict] = []
        pm = await self._get_portfolio_manager()
        practice_positions = {p.ticker: p for p in portfolio if not p.is_real}

        for pick in picks.picks:
            if pick.action != "buy":
                continue
            amount = round(budget_eur * (pick.allocation_pct / 100.0), 2)
            if amount <= 0:
                continue
            price = await self._fetch_price(pick.ticker)
            if price <= 0:
                executions.append(
                    {"status": "skipped", "reason": "missing_price", "ticker": pick.ticker}
                )
                continue
            if not force and await pm.trade_exists(
                llm.value, picks.pick_date, pick.ticker, "buy", False
            ):
                executions.append(
                    {"status": "skipped", "reason": "duplicate", "ticker": pick.ticker}
                )
                continue
            # Route to T212 practice account via place_buy_order with is_real=False
            result = await self._trading_client.call_tool(
                "place_buy_order",
                {
                    "llm_name": llm.value,
                    "ticker": pick.ticker,
                    "amount_eur": amount,
                    "current_price": price,
                    "is_real": False,
                },
            )
            executions.append(result)

        for pick in picks.sell_recommendations:
            ticker = pick.ticker
            position = practice_positions.get(ticker)
            if not position:
                continue
            quantity = float(position.quantity)
            if quantity <= 0:
                continue
            if not force and await pm.trade_exists(
                llm.value, picks.pick_date, ticker, "sell", False
            ):
                executions.append({"status": "skipped", "reason": "duplicate", "ticker": ticker})
                continue
            result = await self._trading_client.call_tool(
                "place_sell_order",
                {"llm_name": llm.value, "ticker": ticker, "quantity": quantity, "is_real": False},
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
                    elif source == "insider":
                        evidence = candidate.get("insider", {})
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

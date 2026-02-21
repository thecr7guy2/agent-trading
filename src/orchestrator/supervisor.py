import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from src.agents.pipeline import AgentPipeline
from src.config import Settings, get_settings
from src.mcp_servers.trading.portfolio import get_demo_positions, get_live_positions
from src.mcp_servers.trading.t212_client import T212Client
from src.models import (
    DailyPicks,
    LLMProvider,
    PickReview,
    Position,
    ResearchReport,
)
from src.notifications.telegram import TelegramNotifier
from src.orchestrator.mcp_client import (
    MCPToolClient,
    create_market_data_client,
    create_reddit_client,
    create_trading_client,
)
from src.orchestrator.rotation import is_trading_day
from src.orchestrator.sell_strategy import SellStrategyEngine
from src.orchestrator.trade_executor import ExecutionSummary, execute_with_fallback
from src.utils.recently_traded import get_blacklist

logger = logging.getLogger(__name__)

# Reddit noise: common acronyms, indices, and ETFs that aren't individual stock picks
_NOISE_TICKERS = {
    "FAQ", "DD", "CEO", "GDP", "IPO", "ATH", "ATL", "IMO", "YOLO", "FYI",
    "EPS", "USA", "USD", "EUR", "GBP", "ETF", "SEC", "FED", "CPI", "PPI",
    "FOMC", "HODL", "DCA", "OEM", "LLC", "INC", "YOY", "QOQ", "MOM",
    "RIP", "FUD", "APE", "TLDR",
}
_INDEX_TICKERS = {"VIX", "GSPC", "DJI", "IXIC", "FTSE", "DAX", "CAC"}
_COMMON_ETFS = {
    "VOO", "SPY", "QQQ", "SCHD", "VTI", "VEA", "VXUS", "BND", "VIG",
    "IWM", "DIA", "ARKK", "ARKW", "ARKG", "VGT", "SOXL", "SOXS", "TQQQ",
    "SQQQ", "VT", "QQQM", "JEPI", "JEPQ", "RSP", "XLF", "XLE", "XLK",
    "VYM", "VNQ", "GLD", "SLV", "TLT", "HYG", "LQD", "AGG", "EFA", "EEM",
    "IEMG", "SCHG", "QQQI", "SPYI", "VWCE", "NEOS", "IWDA", "VUSA",
    "CSPX", "VUAA", "VWRL", "SWDA",
}
_EXCLUDED = _NOISE_TICKERS | _INDEX_TICKERS | _COMMON_ETFS


def _is_valid_stock_ticker(ticker: str) -> bool:
    upper = ticker.upper()
    if upper in _EXCLUDED:
        return False
    if len(ticker) <= 2:
        return False
    return True


def _select_candidates(candidates: dict[str, dict], limit: int) -> list[dict]:
    """Rank and deduplicate candidates from multiple signal sources."""
    multi_source = sorted(
        [c for c in candidates.values() if len(c.get("sources", [])) >= 2],
        key=lambda c: (len(c["sources"]), c.get("reddit_mentions", 0)),
        reverse=True,
    )

    reddit_only = sorted(
        [c for c in candidates.values() if c.get("sources") == ["reddit"]],
        key=lambda c: c.get("reddit_mentions", 0),
        reverse=True,
    )
    screener_only = [c for c in candidates.values() if c.get("sources") == ["screener"]]
    earnings_only = [c for c in candidates.values() if c.get("sources") == ["earnings"]]
    insider_only = sorted(
        [c for c in candidates.values() if c.get("sources") == ["insider"]],
        key=lambda c: c.get("insider", {}).get("total_value", 0),
        reverse=True,
    )

    result = list(multi_source[:limit])
    remaining = limit - len(result)
    if remaining <= 0:
        return result[:limit]

    screener_quota = max(min(remaining * 2 // 5, len(screener_only)), min(8, len(screener_only), remaining))
    result.extend(screener_only[:screener_quota])
    remaining -= screener_quota

    earnings_quota = min(max(remaining // 5, 1), len(earnings_only), remaining)
    result.extend(earnings_only[:earnings_quota])
    remaining -= earnings_quota

    insider_quota = min(max(remaining // 5, 1), len(insider_only), remaining)
    result.extend(insider_only[:insider_quota])
    remaining -= insider_quota

    result.extend(reddit_only[:remaining])
    return result[:limit]


@dataclass
class PipelineResult:
    llm: LLMProvider
    picks: PickReview | DailyPicks
    research: ResearchReport | None
    portfolio: list[dict]


class Supervisor:
    def __init__(
        self,
        settings: Settings | None = None,
        trading_client: MCPToolClient | None = None,
        reddit_client: MCPToolClient | None = None,
        market_data_client: MCPToolClient | None = None,
    ):
        self._settings = settings or get_settings()
        self._pipelines: dict[tuple, AgentPipeline] = {}
        self._trading_client = trading_client
        self._reddit_client = reddit_client
        self._market_data_client = market_data_client
        self._sell_engine = SellStrategyEngine(self._settings)
        self._notifier = TelegramNotifier(self._settings)
        self._t212_live: T212Client | None = None
        self._t212_demo: T212Client | None = None

    def _ensure_clients(self) -> None:
        if self._trading_client is None:
            self._trading_client = create_trading_client()
        if self._reddit_client is None:
            self._reddit_client = create_reddit_client()
        if self._market_data_client is None:
            self._market_data_client = create_market_data_client()

    def _get_t212_live(self) -> T212Client:
        if self._t212_live is None:
            self._t212_live = T212Client(
                api_key=self._settings.t212_api_key,
                api_secret=self._settings.t212_api_secret,
                use_demo=False,
            )
        return self._t212_live

    def _get_t212_demo(self) -> T212Client | None:
        if not self._settings.t212_practice_api_key:
            return None
        if self._t212_demo is None:
            self._t212_demo = T212Client(
                api_key=self._settings.t212_practice_api_key,
                api_secret=self._settings.t212_practice_api_secret or "",
                use_demo=True,
            )
        return self._t212_demo

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
        """Merge signals from Reddit, screener, earnings, and insider sources."""
        self._ensure_clients()
        reddit_digest = await self.build_reddit_digest(subreddits)
        candidates: dict[str, dict] = {}
        screener_count = 0

        # Reddit signals
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

        # Screener signals (global markets with EU soft bonus)
        try:
            screener_result = await asyncio.wait_for(
                self._market_data_client.call_tool("screen_global_markets", {}),
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
            logger.exception("Screener call failed — continuing with Reddit-only")

        # Earnings calendar signals
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
            logger.exception("Earnings calendar call failed — continuing without it")

        # Insider buying signals (OpenInsider cluster buys)
        try:
            insider_result = await asyncio.wait_for(
                self._market_data_client.call_tool("get_insider_activity", {"days": 7}),
                timeout=60.0,
            )
            for cluster in insider_result.get("cluster_buys", []):
                ticker = cluster.get("ticker", "")
                if not ticker or not _is_valid_stock_ticker(ticker):
                    continue
                if ticker in candidates:
                    candidates[ticker]["sources"].append("insider")
                    candidates[ticker]["insider"] = cluster
                else:
                    candidates[ticker] = {
                        "ticker": ticker,
                        "sources": ["insider"],
                        "reddit_mentions": 0,
                        "sentiment_score": 0.0,
                        "insider": cluster,
                    }
        except Exception:
            logger.exception("Insider activity call failed — continuing without it")

        limit = self._settings.max_candidates
        sorted_candidates = _select_candidates(candidates, limit)

        # Enrich candidates with recent news headlines
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

    async def run_decision_cycle(
        self,
        run_date: date | None = None,
        require_approval: bool = False,
        force: bool = False,
        collect_rounds: int = 0,
    ) -> dict:
        run_date = run_date or datetime.now(ZoneInfo(self._settings.orchestrator_timezone)).date()
        if not force and not is_trading_day(run_date, self._settings.orchestrator_timezone):
            return {"status": "skipped", "reason": "non-trading-day", "date": str(run_date)}

        if collect_rounds > 0:
            await self.collect_reddit_round()

        digest = await self.build_signal_digest()
        if digest.get("error"):
            return {"status": "error", "stage": "signal_digest", "error": digest["error"]}

        # Filter blacklisted tickers (bought in last N days)
        blacklist = get_blacklist(
            path=self._settings.recently_traded_path,
            days=self._settings.recently_traded_days,
        )
        all_candidates = digest.get("candidates", [])
        blacklisted_candidates: list[str] = []
        if blacklist:
            blacklisted_candidates = [
                c.get("ticker") for c in all_candidates if c.get("ticker") in blacklist
            ]
            digest["candidates"] = [c for c in all_candidates if c.get("ticker") not in blacklist]
            if blacklisted_candidates:
                logger.info("Filtered %d blacklisted tickers", len(blacklisted_candidates))

        # Run research + decision pipelines
        pipeline_results = await self._run_pipelines(digest, run_date)
        result_by_llm = {item.llm: item for item in pipeline_results}

        conservative_result = result_by_llm.get(LLMProvider.CLAUDE)
        aggressive_result = result_by_llm.get(LLMProvider.CLAUDE_AGGRESSIVE)

        if conservative_result is None:
            return {"status": "error", "stage": "pipeline", "error": "conservative pipeline failed"}

        # Execute conservative → real T212 live account
        t212_live = self._get_t212_live()
        real_candidates = await self._picks_to_candidates(conservative_result.picks)
        real_summary = await execute_with_fallback(
            candidates=real_candidates,
            is_real=True,
            t212=t212_live,
        )

        # Execute aggressive → T212 practice account
        practice_summary: ExecutionSummary | None = None
        t212_demo = self._get_t212_demo()
        if t212_demo and aggressive_result:
            practice_candidates = await self._picks_to_candidates(aggressive_result.picks)
            practice_summary = await execute_with_fallback(
                candidates=practice_candidates,
                is_real=False,
                t212=t212_demo,
            )

        real_exec = self._summary_to_list(real_summary)
        practice_exec = self._summary_to_list(practice_summary)

        decision_result = {
            "status": "ok",
            "date": str(run_date),
            "conservative_trader": LLMProvider.CLAUDE.value,
            "aggressive_trader": LLMProvider.CLAUDE_AGGRESSIVE.value,
            "approval": {"action": "approve_all", "approved_indices": [], "timed_out": False},
            "reddit_posts": digest.get("total_posts", 0),
            "tickers_analyzed": len(digest.get("candidates", [])),
            "blacklisted_candidates": blacklisted_candidates,
            "real_execution": real_exec,
            "practice_execution": practice_exec,
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

    async def _picks_to_candidates(self, picks: PickReview | DailyPicks) -> list[dict]:
        """Convert pipeline picks to a ranked candidates list for execute_with_fallback.

        Sorted by allocation_pct descending — top conviction pick first.
        """
        self._ensure_clients()
        buy_picks = sorted(
            [p for p in picks.picks if p.action == "buy"],
            key=lambda p: p.allocation_pct,
            reverse=True,
        )

        candidates: list[dict] = []
        for pick in buy_picks:
            price = await self._fetch_price(pick.ticker)
            if price > 0:
                candidates.append({
                    "ticker": pick.ticker,
                    "price": price,
                    "allocation_pct": pick.allocation_pct,
                    "reasoning": pick.reasoning,
                })
            else:
                logger.warning("No price for %s — excluded from execution candidates", pick.ticker)

        return candidates

    @staticmethod
    def _summary_to_list(summary: ExecutionSummary | None) -> list[dict]:
        if summary is None:
            return []
        result = []
        for r in summary.bought:
            result.append({
                "status": "filled",
                "ticker": r.ticker,
                "amount_eur": r.amount_spent,
                "quantity": r.quantity,
                "broker_ticker": r.broker_ticker,
            })
        for r in summary.failed:
            result.append({
                "status": "failed",
                "ticker": r.ticker,
                "error": r.error,
            })
        return result

    @staticmethod
    def _build_analysis_summary(result: PipelineResult) -> dict:
        picks_obj = result.picks
        picked_tickers = {p.ticker for p in picks_obj.picks}

        pick_reasoning = [
            {
                "ticker": p.ticker,
                "action": p.action,
                "allocation_pct": p.allocation_pct,
                "reasoning": p.reasoning,
            }
            for p in picks_obj.picks
        ]

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
        timeout = self._settings.pipeline_timeout_seconds
        practice_budget = self._settings.practice_daily_budget_eur

        # Stages 1-2: run research once, shared across both strategies
        research_pipeline = self._get_pipeline(LLMProvider.CLAUDE, "conservative")
        try:
            sentiment, research = await asyncio.wait_for(
                research_pipeline.run_research(digest),
                timeout=float(timeout),
            )
        except TimeoutError:
            logger.error("Shared research phase timed out after %ds", timeout)
            return []
        except Exception:
            logger.exception("Shared research phase failed")
            return []

        shared_research = research if isinstance(research, ResearchReport) else None

        # Stages 3-4: fan out to both strategies in parallel using shared research
        async def _run_decision(
            llm: LLMProvider, strategy: str, budget: float
        ) -> PipelineResult | None:
            try:
                # Get current positions from T212 as portfolio context for the trader
                portfolio_dicts = await self._get_portfolio_dicts(llm)
                output = await asyncio.wait_for(
                    self._get_pipeline(llm, strategy).run_decision(
                        sentiment,
                        research,
                        portfolio_dicts,
                        budget,
                        run_date,
                    ),
                    timeout=float(timeout),
                )
                return PipelineResult(
                    llm=llm,
                    picks=output.picks,
                    research=shared_research,
                    portfolio=portfolio_dicts,
                )
            except TimeoutError:
                logger.error("Decision stage timed out for %s after %ds", llm.value, timeout)
                return None
            except Exception:
                logger.exception("Decision stage failed for %s", llm.value)
                return None

        results = await asyncio.gather(
            _run_decision(LLMProvider.CLAUDE, "conservative", self._settings.daily_budget_eur),
            _run_decision(LLMProvider.CLAUDE_AGGRESSIVE, "aggressive", practice_budget),
        )
        return [r for r in results if r is not None]

    async def _get_portfolio_dicts(self, llm: LLMProvider) -> list[dict]:
        """Fetch current positions from T212 and convert to dicts for trader context."""
        try:
            is_real = llm == LLMProvider.CLAUDE
            if is_real:
                t212 = self._get_t212_live()
                positions = await get_live_positions(t212)
            else:
                t212 = self._get_t212_demo()
                if t212 is None:
                    return []
                positions = await get_demo_positions(t212)
            return positions  # already dicts from _normalise_positions
        except Exception:
            logger.exception("Failed to fetch portfolio for %s", llm.value)
            return []

    def _get_pipeline(self, llm: LLMProvider, strategy: str = "conservative") -> AgentPipeline:
        key = (llm, strategy)
        pipeline = self._pipelines.get(key)
        if pipeline is None:
            pipeline = AgentPipeline(
                llm,
                market_data_client=self._market_data_client,
                trading_client=self._trading_client,
                strategy=strategy,
            )
            self._pipelines[key] = pipeline
        return pipeline

    async def _fetch_price(self, ticker: str) -> float:
        self._ensure_clients()
        try:
            price_resp = await asyncio.wait_for(
                self._market_data_client.call_tool("get_stock_price", {"ticker": ticker}),
                timeout=15.0,
            )
        except TimeoutError:
            logger.warning("Price fetch timed out for %s", ticker)
            price_resp = {}
        return self._extract_price(price_resp)

    async def run_end_of_day(self, run_date: date | None = None) -> dict:
        """Fetch current T212 positions and calculate portfolio snapshot."""
        self._ensure_clients()
        run_date = run_date or datetime.now(ZoneInfo(self._settings.orchestrator_timezone)).date()
        snapshots = {}
        live_positions: list[dict] = []
        demo_positions: list[dict] = []

        async def _snapshot(label: str, positions: list[dict]) -> dict:
            total_invested = Decimal("0")
            total_value = Decimal("0")
            for pos in positions:
                qty = Decimal(str(pos.get("quantity", 0)))
                avg = Decimal(str(pos.get("avg_buy_price", 0)))
                invested = qty * avg
                total_invested += invested
                current = pos.get("current_price", 0.0)
                if current > 0:
                    total_value += qty * Decimal(str(current))
                else:
                    total_value += invested
            unrealized = total_value - total_invested
            return {
                "total_invested": str(round(total_invested, 2)),
                "total_value": str(round(total_value, 2)),
                "unrealized_pnl": str(round(unrealized, 2)),
            }

        try:
            t212_live = self._get_t212_live()
            live_positions = await get_live_positions(t212_live)
            snapshots["conservative_real"] = await _snapshot("conservative_real", live_positions)
        except Exception:
            logger.exception("Failed to fetch live positions for EOD snapshot")

        t212_demo = self._get_t212_demo()
        if t212_demo:
            try:
                demo_positions = await get_demo_positions(t212_demo)
                snapshots["aggressive_demo"] = await _snapshot("aggressive_demo", demo_positions)
            except Exception:
                logger.exception("Failed to fetch demo positions for EOD snapshot")

        return {
            "status": "ok",
            "date": str(run_date),
            "snapshots": snapshots,
            "live_positions": live_positions,
            "demo_positions": demo_positions,
        }

    async def run_sell_checks(
        self,
        run_date: date | None = None,
        include_real: bool = True,
        include_virtual: bool = True,
    ) -> dict:
        """Evaluate sell rules against current T212 positions and execute signals."""
        self._ensure_clients()
        run_date = run_date or datetime.now(ZoneInfo(self._settings.orchestrator_timezone)).date()

        positions: list[Position] = []

        if include_real:
            try:
                t212_live = self._get_t212_live()
                live_dicts = await get_live_positions(t212_live)
                for p in live_dicts:
                    positions.append(self._dict_to_position(p, is_real=True, llm=LLMProvider.CLAUDE))
            except Exception:
                logger.exception("Failed to fetch live positions for sell check")

        if include_virtual:
            t212_demo = self._get_t212_demo()
            if t212_demo:
                try:
                    demo_dicts = await get_demo_positions(t212_demo)
                    for p in demo_dicts:
                        positions.append(
                            self._dict_to_position(p, is_real=False, llm=LLMProvider.CLAUDE_AGGRESSIVE)
                        )
                except Exception:
                    logger.exception("Failed to fetch demo positions for sell check")

        if not positions:
            return {"status": "ok", "date": str(run_date), "executed_sells": []}

        # Fetch current prices for all unique tickers
        tickers = list({p.ticker for p in positions})
        prices: dict[str, float] = {}
        for ticker in tickers:
            prices[ticker] = await self._fetch_price(ticker)

        signals = self._sell_engine.evaluate_positions(positions, prices, run_date)
        if not signals:
            return {"status": "ok", "date": str(run_date), "executed_sells": []}

        executed_sells: list[dict] = []
        for signal in signals:
            result = await self._execute_sell_signal(signal)
            executed_sells.append(result)

        sell_result = {
            "status": "ok",
            "date": str(run_date),
            "executed_sells": executed_sells,
        }
        await self._notifier.notify_sell_signals(sell_result)
        return sell_result

    async def _execute_sell_signal(self, signal) -> dict:
        self._ensure_clients()
        quantity = float(signal.position_qty)

        result = await self._trading_client.call_tool(
            "place_sell_order",
            {
                "ticker": signal.ticker,
                "quantity": quantity,
                "is_real": signal.is_real,
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
            "real" if signal.is_real else "demo",
        )
        return result

    @staticmethod
    def _dict_to_position(p: dict, is_real: bool, llm: LLMProvider) -> Position:
        """Convert a normalised T212 position dict to a Position model."""
        import re
        from datetime import date as date_type

        opened_at = None
        raw_date = p.get("open_date", "")
        if raw_date:
            # T212 dates may be ISO timestamps like "2026-02-18T09:31:22Z"
            match = re.match(r"(\d{4}-\d{2}-\d{2})", str(raw_date))
            if match:
                try:
                    opened_at = date_type.fromisoformat(match.group(1))
                except ValueError:
                    pass

        return Position(
            ticker=p.get("ticker", ""),
            quantity=Decimal(str(p.get("quantity", 0))),
            avg_buy_price=Decimal(str(p.get("avg_buy_price", 0))),
            current_price=float(p.get("current_price", 0)),
            is_real=is_real,
            llm_name=llm,
            opened_at=opened_at,
        )

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

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from src.agents.pipeline import AgentPipeline, PipelineOutput
from src.config import Settings, get_settings
from src.mcp_servers.market_data.capitol_trades import get_politician_candidates
from src.mcp_servers.market_data.finance import (
    get_eur_usd_rate,
    get_price_return_pct,
    get_technical_indicators_for_ticker,
    get_ticker_earnings,
    get_ticker_fundamentals,
    get_ticker_news,
)
from src.mcp_servers.market_data.insider import get_insider_candidates, get_ticker_insider_history
from src.mcp_servers.market_data.news import get_company_news
from src.mcp_servers.trading.portfolio import get_demo_positions
from src.mcp_servers.trading.t212_client import T212Client
from src.models import PickReview
from src.notifications.telegram import TelegramNotifier
from src.orchestrator.mcp_client import (
    MCPToolClient,
    create_market_data_client,
    create_trading_client,
)
from src.orchestrator.rotation import is_trading_day
from src.orchestrator.trade_executor import ExecutionSummary, execute_with_fallback
from src.utils.recently_traded import get_blacklist

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    picks: PickReview
    research: object
    portfolio: list[dict]


class Supervisor:
    def __init__(
        self,
        settings: Settings | None = None,
        trading_client: MCPToolClient | None = None,
        market_data_client: MCPToolClient | None = None,
    ):
        self._settings = settings or get_settings()
        self._pipeline: AgentPipeline | None = None
        self._trading_client = trading_client
        self._market_data_client = market_data_client
        self._notifier = TelegramNotifier(self._settings)
        self._t212: T212Client | None = None
        self._news_sem = asyncio.Semaphore(5)  # max 5 concurrent NewsAPI calls

    def _ensure_clients(self) -> None:
        if self._trading_client is None:
            self._trading_client = create_trading_client()
        if self._market_data_client is None:
            self._market_data_client = create_market_data_client()

    def _get_t212(self) -> T212Client:
        if self._t212 is None:
            self._t212 = T212Client(
                api_key=self._settings.t212_api_key,
                api_secret=self._settings.t212_api_secret,
                use_demo=True,
            )
        return self._t212

    def _get_pipeline(self) -> AgentPipeline:
        if self._pipeline is None:
            self._pipeline = AgentPipeline()
        return self._pipeline

    async def _enrich_candidate(self, candidate: dict) -> dict:
        """Fetch all enrichment data for a single insider candidate in parallel."""
        ticker = candidate["ticker"]
        company = candidate.get("company", ticker)

        async def _safe(coro, default=None):
            try:
                return await asyncio.wait_for(coro, timeout=20.0)
            except Exception:
                return default

        (
            returns_1m,
            returns_6m,
            returns_1y,
            fundamentals,
            technicals,
            earnings,
            insider_history,
        ) = await asyncio.gather(
            _safe(get_price_return_pct(ticker, "1mo"), 0.0),
            _safe(get_price_return_pct(ticker, "6mo"), 0.0),
            _safe(get_price_return_pct(ticker, "1y"), 0.0),
            _safe(get_ticker_fundamentals(ticker), {}),
            _safe(get_technical_indicators_for_ticker(ticker), {}),
            _safe(get_ticker_earnings(ticker), {}),
            _safe(get_ticker_insider_history(ticker, days=90), {}),
        )

        # News — try NewsAPI first (rate-limited), fall back to yfinance
        news = []
        try:
            settings = self._settings
            if settings.news_api_key:
                async with self._news_sem:
                    news = await asyncio.wait_for(
                        get_company_news(company, ticker, settings.news_api_key, max_items=5),
                        timeout=10.0,
                    )
            if not news:
                news = await asyncio.wait_for(get_ticker_news(ticker, max_items=5), timeout=10.0)
        except Exception:
            pass

        return {
            **candidate,
            "returns": {
                "return_1m": returns_1m,
                "return_6m": returns_6m,
                "return_1y": returns_1y,
            },
            "fundamentals": fundamentals,
            "technicals": technicals,
            "earnings": earnings,
            "insider_history": insider_history,
            "news": news,
        }

    async def build_insider_digest(self) -> dict:
        """
        Build the enriched insider digest:
        1. Fetch top N candidates from OpenInsider + Capitol Trades (in parallel if enabled)
        2. Merge by ticker, tag sources, deduplicate
        3. Parallel-enrich all candidates with yfinance + news + insider history
        """
        settings = self._settings
        logger.info(
            "Fetching insider candidates (lookback %dd, top %d)",
            settings.insider_lookback_days,
            settings.insider_top_n,
        )

        if settings.capitol_trades_enabled:
            insider_task = get_insider_candidates(
                days=settings.insider_lookback_days,
                top_n=settings.insider_top_n,
            )
            politician_task = get_politician_candidates(
                lookback_days=settings.capitol_trades_lookback_days,
                top_n=settings.capitol_trades_top_n,
            )
            insider_candidates, politician_candidates = await asyncio.gather(
                insider_task, politician_task
            )
            logger.info(
                "Got %d OpenInsider + %d Capitol Trades candidates before enrichment",
                len(insider_candidates),
                len(politician_candidates),
            )
        else:
            insider_candidates = await get_insider_candidates(
                days=settings.insider_lookback_days,
                top_n=settings.insider_top_n,
            )
            politician_candidates = []
            logger.info("Got %d insider candidates before enrichment", len(insider_candidates))

        # Tag OpenInsider candidates with source
        for c in insider_candidates:
            c.setdefault("source", "openinsider")

        # Merge by ticker — same ticker in both sources becomes a combined entry
        ticker_to_candidate: dict[str, dict] = {}
        for c in insider_candidates:
            ticker_to_candidate[c["ticker"]] = c

        for p in politician_candidates:
            ticker = p["ticker"]
            if ticker in ticker_to_candidate:
                existing = ticker_to_candidate[ticker]
                combined_insiders = list(existing.get("insiders", []))
                for name in p["insiders"]:
                    if name not in combined_insiders:
                        combined_insiders.append(name)
                ticker_to_candidate[ticker] = {
                    **existing,
                    "source": "openinsider+capitol_trades",
                    "insiders": combined_insiders,
                    "conviction_score": existing["conviction_score"] + p["conviction_score"],
                    "total_value_usd": existing["total_value_usd"] + p["total_value_usd"],
                    "has_politician_buy": True,
                    "politician_names": p["insiders"],
                }
            else:
                ticker_to_candidate[ticker] = p

        candidates = list(ticker_to_candidate.values())

        if not candidates:
            return {"candidates": [], "insider_count": 0}

        # Parallel enrichment — all candidates simultaneously
        enriched = await asyncio.gather(*[self._enrich_candidate(c) for c in candidates])
        enriched_list = list(enriched)

        # Drop non-equity instruments (mutual funds, ETFs, indices) that slip through
        non_equity = {"MUTUALFUND", "ETF", "INDEX", "FUTURE", "CURRENCY"}
        equity_list = [
            c
            for c in enriched_list
            if c.get("fundamentals", {}).get("quote_type", "EQUITY").upper() not in non_equity
        ]
        dropped = len(enriched_list) - len(equity_list)
        if dropped:
            logger.info("Dropped %d non-equity candidates (mutual funds/ETFs/indices)", dropped)

        # Drop mega-cap Capitol Trades candidates — politician buys in AAPL/GOOGL/META
        # are routine portfolio allocation, not information-driven signals
        cap_ceiling = settings.capitol_trades_max_market_cap
        pre_filter = len(equity_list)
        filtered_list = []
        for c in equity_list:
            if c.get("source") == "capitol_trades":
                mcap = c.get("fundamentals", {}).get("market_cap") or 0
                if mcap > cap_ceiling:
                    logger.debug(
                        "Dropped Capitol Trades candidate %s (market cap $%.0fB > $%.0fB ceiling)",
                        c["ticker"],
                        mcap / 1e9,
                        cap_ceiling / 1e9,
                    )
                    continue
            filtered_list.append(c)
        equity_list = filtered_list
        ct_dropped = pre_filter - len(equity_list)
        if ct_dropped:
            logger.info(
                "Dropped %d Capitol Trades mega-cap candidates (market cap > $%.0fB)",
                ct_dropped,
                cap_ceiling / 1e9,
            )

        source_counts = {
            "openinsider": sum(1 for c in equity_list if "openinsider" in c.get("source", "")),
            "capitol_trades": sum(
                1 for c in equity_list if "capitol_trades" in c.get("source", "")
            ),
        }
        logger.info(
            "Enrichment complete: %d candidates (openinsider=%d, capitol_trades=%d)",
            len(equity_list),
            source_counts["openinsider"],
            source_counts["capitol_trades"],
        )
        return {
            "candidates": equity_list,
            "insider_count": len(equity_list),
            "lookback_days": settings.insider_lookback_days,
            "source_counts": source_counts,
        }

    async def run_decision_cycle(
        self,
        run_date: date | None = None,
        force: bool = False,
    ) -> dict:
        run_date = run_date or datetime.now(ZoneInfo(self._settings.orchestrator_timezone)).date()

        if not force and not is_trading_day(run_date, self._settings.orchestrator_timezone):
            return {"status": "skipped", "reason": "non-trading-day", "date": str(run_date)}

        # Build enriched insider digest
        digest = await self.build_insider_digest()
        insider_count = digest.get("insider_count", 0)

        if insider_count < self._settings.min_insider_tickers:
            logger.info(
                "Low signal day — only %d insider candidates (min %d). Skipping buys.",
                insider_count,
                self._settings.min_insider_tickers,
            )
            return {
                "status": "skipped",
                "reason": f"low signal day: {insider_count} candidates < min {self._settings.min_insider_tickers}",
                "date": str(run_date),
            }

        # Filter blacklisted tickers
        blacklist = get_blacklist(
            path=self._settings.recently_traded_path,
            days=self._settings.recently_traded_days,
        )
        all_candidates = digest.get("candidates", [])
        blacklisted = [c["ticker"] for c in all_candidates if c["ticker"] in blacklist]
        filtered = [c for c in all_candidates if c["ticker"] not in blacklist]
        if blacklisted:
            logger.info("Filtered %d blacklisted tickers: %s", len(blacklisted), blacklisted)

        # Pool-aware cap: guarantee Capitol Trades slots reach the research stage
        if self._settings.capitol_trades_enabled:
            insider_pool = [c for c in filtered if c.get("source") != "capitol_trades"]
            politician_pool = [c for c in filtered if c.get("source") == "capitol_trades"]
            reserved = self._settings.capitol_trades_reserved_slots
            politician_slots = min(len(politician_pool), reserved)
            insider_slots = self._settings.research_top_n - politician_slots
            capped = politician_pool[:politician_slots] + insider_pool[:insider_slots]
        else:
            capped = filtered[: self._settings.research_top_n]

        digest["candidates"] = capped
        if len(filtered) > self._settings.research_top_n:
            logger.info(
                "Capped candidates from %d to %d for research stage",
                len(filtered),
                self._settings.research_top_n,
            )

        # Get current portfolio
        t212 = self._get_t212()
        try:
            portfolio = await get_demo_positions(t212)
        except Exception:
            logger.exception("Failed to fetch demo positions")
            portfolio = []

        # Run pipeline
        pipeline = self._get_pipeline()
        try:
            output: PipelineOutput = await asyncio.wait_for(
                pipeline.run(
                    enriched_digest=digest,
                    portfolio=portfolio,
                    budget_eur=self._settings.budget_per_run_eur,
                    run_date=run_date,
                ),
                timeout=float(self._settings.pipeline_timeout_seconds),
            )
        except TimeoutError:
            logger.error("Pipeline timed out after %ds", self._settings.pipeline_timeout_seconds)
            await self._notifier.notify_error(
                str(run_date),
                "pipeline",
                f"timeout after {self._settings.pipeline_timeout_seconds}s",
            )
            return {
                "status": "error",
                "stage": "pipeline",
                "error": "timeout",
                "date": str(run_date),
            }
        except Exception as e:
            logger.exception("Pipeline failed")
            await self._notifier.notify_error(str(run_date), "pipeline", str(e))
            return {"status": "error", "stage": "pipeline", "date": str(run_date)}

        # Execute trades on demo account
        candidates = await self._picks_to_candidates(output.picks)
        summary = await execute_with_fallback(candidates=candidates, t212=t212)

        result = {
            "status": "ok",
            "date": str(run_date),
            "insider_count": insider_count,
            "blacklisted": blacklisted,
            "execution": self._summary_to_list(summary),
            "picks": [
                {
                    "ticker": p.ticker,
                    "action": p.action,
                    "allocation_pct": p.allocation_pct,
                    "reasoning": p.reasoning,
                }
                for p in output.picks.picks
            ],
            "confidence": output.picks.confidence,
            "market_summary": output.picks.market_summary,
        }

        await self._notifier.notify_daily_summary(result)
        return result

    async def _picks_to_candidates(self, picks: PickReview) -> list[dict]:
        self._ensure_clients()
        buy_picks = sorted(
            [p for p in picks.picks if p.action == "buy"],
            key=lambda p: p.allocation_pct,
            reverse=True,
        )
        buy_picks = buy_picks[: self._settings.max_picks_per_run]

        eur_usd = await get_eur_usd_rate()
        logger.info("EUR/USD rate for order sizing: %.4f", eur_usd)

        candidates: list[dict] = []
        for pick in buy_picks:
            price, currency = await self._fetch_price(pick.ticker)
            if price <= 0:
                logger.warning("No price for %s — excluded from execution", pick.ticker)
                continue
            # Convert to EUR so quantity = amount_eur / price_eur is correct
            price_eur = price / eur_usd if currency == "USD" else price
            candidates.append(
                {
                    "ticker": pick.ticker,
                    "price": price_eur,
                    "allocation_pct": pick.allocation_pct,
                    "reasoning": pick.reasoning,
                }
            )
        return candidates

    @staticmethod
    def _summary_to_list(summary: ExecutionSummary) -> list[dict]:
        result = []
        for r in summary.bought:
            result.append(
                {
                    "status": "filled",
                    "ticker": r.ticker,
                    "amount_eur": r.amount_spent,
                    "quantity": r.quantity,
                    "broker_ticker": r.broker_ticker,
                }
            )
        for r in summary.failed:
            result.append({"status": "failed", "ticker": r.ticker, "error": r.error})
        return result

    async def _fetch_price(self, ticker: str) -> tuple[float, str]:
        """Returns (price, currency) where price is in the stock's native currency."""
        self._ensure_clients()
        try:
            price_resp = await asyncio.wait_for(
                self._market_data_client.call_tool("get_stock_price", {"ticker": ticker}),
                timeout=15.0,
            )
        except TimeoutError:
            logger.warning("Price fetch timed out for %s", ticker)
            price_resp = {}
        price = self._extract_price(price_resp)
        currency = price_resp.get("currency", "USD") if isinstance(price_resp, dict) else "USD"
        return price, currency

    async def run_end_of_day(self, run_date: date | None = None) -> dict:
        run_date = run_date or datetime.now(ZoneInfo(self._settings.orchestrator_timezone)).date()
        t212 = self._get_t212()
        positions: list[dict] = []
        try:
            positions = await get_demo_positions(t212)
        except Exception:
            logger.exception("Failed to fetch demo positions for EOD snapshot")

        total_invested = Decimal("0")
        total_value = Decimal("0")
        for pos in positions:
            qty = Decimal(str(pos.get("quantity", 0)))
            avg = Decimal(str(pos.get("avg_buy_price", 0)))
            total_invested += qty * avg
            current = pos.get("current_price", 0.0)
            total_value += qty * Decimal(str(current)) if current > 0 else qty * avg

        return {
            "status": "ok",
            "date": str(run_date),
            "snapshots": {
                "demo": {
                    "total_invested": str(round(total_invested, 2)),
                    "total_value": str(round(total_value, 2)),
                    "unrealized_pnl": str(round(total_value - total_invested, 2)),
                }
            },
            "demo_positions": positions,
        }

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

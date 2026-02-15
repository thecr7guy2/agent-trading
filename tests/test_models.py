from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.db.models import (
    AgentStage,
    DailyPicks,
    LLMProvider,
    PnLReport,
    Position,
    SentimentReport,
    StockPick,
    TickerAnalysis,
    TickerSentiment,
    Trade,
    TradeAction,
    TradeStatus,
)


class TestTickerSentiment:
    def test_valid(self):
        ts = TickerSentiment(ticker="ASML.AS", mention_count=42, sentiment_score=0.8)
        assert ts.ticker == "ASML.AS"
        assert ts.mention_count == 42
        assert ts.sentiment_score == 0.8

    def test_sentiment_bounds(self):
        with pytest.raises(ValidationError):
            TickerSentiment(ticker="X", sentiment_score=1.5)
        with pytest.raises(ValidationError):
            TickerSentiment(ticker="X", sentiment_score=-1.5)

    def test_defaults(self):
        ts = TickerSentiment(ticker="SAP.DE", sentiment_score=0.0)
        assert ts.mention_count == 0
        assert ts.top_quotes == []
        assert ts.subreddits == {}


class TestSentimentReport:
    def test_valid(self):
        report = SentimentReport(
            report_date=date(2026, 2, 14),
            tickers=[TickerSentiment(ticker="ASML.AS", sentiment_score=0.5)],
            total_posts_analyzed=100,
            subreddits_scraped=["wallstreetbets"],
        )
        assert len(report.tickers) == 1
        assert report.total_posts_analyzed == 100


class TestTickerAnalysis:
    def test_score_bounds(self):
        with pytest.raises(ValidationError):
            TickerAnalysis(ticker="X", fundamental_score=11.0)
        with pytest.raises(ValidationError):
            TickerAnalysis(ticker="X", technical_score=-1.0)

    def test_defaults(self):
        ta = TickerAnalysis(ticker="SAP.DE")
        assert ta.fundamental_score == 0.0
        assert ta.current_price == Decimal("0")


class TestStockPick:
    def test_valid(self):
        pick = StockPick(
            ticker="ASML.AS",
            exchange="Euronext Amsterdam",
            allocation_pct=60.0,
            reasoning="Strong momentum",
            action=TradeAction.BUY,
        )
        assert pick.allocation_pct == 60.0

    def test_allocation_bounds(self):
        with pytest.raises(ValidationError):
            StockPick(ticker="X", allocation_pct=101.0)
        with pytest.raises(ValidationError):
            StockPick(ticker="X", allocation_pct=-5.0)


class TestDailyPicks:
    def test_valid(self):
        picks = DailyPicks(
            llm=LLMProvider.CLAUDE,
            pick_date=date(2026, 2, 14),
            picks=[StockPick(ticker="ASML.AS", allocation_pct=100.0)],
            confidence=0.9,
            market_summary="Bullish day",
        )
        assert picks.llm == "claude"
        assert picks.confidence == 0.9

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            DailyPicks(
                llm=LLMProvider.CLAUDE,
                pick_date=date(2026, 2, 14),
                confidence=1.5,
            )


class TestTrade:
    def test_defaults(self):
        trade = Trade(
            llm_name=LLMProvider.MINIMAX,
            trade_date=date(2026, 2, 14),
            ticker="SAP.DE",
            action=TradeAction.BUY,
        )
        assert trade.status == TradeStatus.PENDING
        assert trade.is_real is False
        assert trade.id is None


class TestPosition:
    def test_valid(self):
        pos = Position(
            llm_name=LLMProvider.CLAUDE,
            ticker="ASML.AS",
            quantity=Decimal("0.5"),
            avg_buy_price=Decimal("850.00"),
            is_real=True,
        )
        assert pos.quantity == Decimal("0.5")


class TestPnLReport:
    def test_valid(self):
        report = PnLReport(
            llm_name=LLMProvider.CLAUDE,
            period_start=date(2026, 2, 10),
            period_end=date(2026, 2, 14),
            total_invested=Decimal("50"),
            total_value=Decimal("52"),
            total_pnl=Decimal("2"),
            return_pct=4.0,
            win_count=3,
            loss_count=1,
            win_rate=75.0,
        )
        assert report.return_pct == 4.0


class TestEnums:
    def test_llm_provider_values(self):
        assert LLMProvider.CLAUDE == "claude"
        assert LLMProvider.MINIMAX == "minimax"

    def test_agent_stage_values(self):
        assert AgentStage.SENTIMENT == "sentiment"
        assert AgentStage.MARKET == "market"
        assert AgentStage.TRADER == "trader"

    def test_trade_action_values(self):
        assert TradeAction.BUY == "buy"
        assert TradeAction.SELL == "sell"

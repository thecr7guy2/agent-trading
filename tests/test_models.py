from datetime import date
from decimal import Decimal

from src.models import (
    AgentStage,
    DailyPicks,
    LLMProvider,
    Position,
    ResearchFinding,
    ResearchReport,
    SentimentReport,
    StockPick,
    TickerSentiment,
)


class TestTickerSentiment:
    def test_valid(self):
        ts = TickerSentiment(ticker="ASML.AS", mentions=42, sentiment_score=0.8)
        assert ts.ticker == "ASML.AS"
        assert ts.mentions == 42
        assert ts.sentiment_score == 0.8

    def test_defaults(self):
        ts = TickerSentiment(ticker="SAP.DE")
        assert ts.mentions == 0
        assert ts.top_quotes == []
        assert ts.subreddits == {}


class TestSentimentReport:
    def test_valid(self):
        report = SentimentReport(
            report_date="2026-02-18",
            tickers=[TickerSentiment(ticker="ASML.AS", sentiment_score=0.5)],
            total_mentions=100,
        )
        assert len(report.tickers) == 1
        assert report.total_mentions == 100

    def test_defaults(self):
        report = SentimentReport()
        assert report.tickers == []
        assert report.report_date == ""


class TestStockPick:
    def test_valid(self):
        pick = StockPick(
            ticker="ASML.AS",
            allocation_pct=60.0,
            reasoning="Strong momentum",
            action="buy",
        )
        assert pick.allocation_pct == 60.0
        assert pick.action == "buy"

    def test_defaults(self):
        pick = StockPick(ticker="SAP.DE")
        assert pick.action == "buy"
        assert pick.allocation_pct == 0.0


class TestDailyPicks:
    def test_valid(self):
        picks = DailyPicks(
            llm=LLMProvider.CLAUDE,
            pick_date=date(2026, 2, 18),
            picks=[StockPick(ticker="ASML.AS", allocation_pct=100.0)],
            confidence=0.9,
            market_summary="Bullish day",
        )
        assert picks.llm == "claude"
        assert picks.confidence == 0.9
        assert len(picks.picks) == 1

    def test_defaults(self):
        picks = DailyPicks()
        assert picks.picks == []
        assert picks.sell_recommendations == []


class TestPosition:
    def test_valid(self):
        pos = Position(
            ticker="ASML.AS",
            quantity=Decimal("0.5"),
            avg_buy_price=Decimal("850.00"),
            is_real=True,
            llm_name=LLMProvider.CLAUDE,
        )
        assert pos.quantity == Decimal("0.5")
        assert pos.is_real is True

    def test_defaults(self):
        pos = Position(ticker="SAP.DE")
        assert pos.quantity == Decimal("0")
        assert pos.is_real is True
        assert pos.opened_at is None


class TestResearchReport:
    def test_valid(self):
        finding = ResearchFinding(
            ticker="ASML.AS",
            exchange="Euronext Amsterdam",
            current_price=850.0,
            pros=["Strong order backlog", "EU semiconductor leader"],
            cons=["High P/E", "Dependent on TSMC"],
        )
        report = ResearchReport(tickers=[finding])
        assert len(report.tickers) == 1
        assert report.tickers[0].ticker == "ASML.AS"
        assert len(report.tickers[0].pros) == 2

    def test_tool_calls_default(self):
        report = ResearchReport()
        assert report.tool_calls_made == 0


class TestEnums:
    def test_llm_provider_values(self):
        assert LLMProvider.CLAUDE == "claude"
        assert LLMProvider.CLAUDE_AGGRESSIVE == "claude_aggressive"

    def test_agent_stage_values(self):
        assert AgentStage.SENTIMENT == "sentiment"
        assert AgentStage.MARKET == "market"
        assert AgentStage.TRADER == "trader"
        assert AgentStage.RESEARCH == "research"
        assert AgentStage.RISK == "risk"

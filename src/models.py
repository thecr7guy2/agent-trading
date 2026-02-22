"""
Shared Pydantic models for the trading bot pipeline.

No database dependencies — models are used purely for data flow between
agent stages and orchestrator components.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class LLMProvider(StrEnum):
    CLAUDE = "claude"
    CLAUDE_AGGRESSIVE = "claude_aggressive"
    MINIMAX = "minimax"


class AgentStage(StrEnum):
    SENTIMENT = "sentiment"
    MARKET = "market"
    RESEARCH = "research"
    TRADER = "trader"
    RISK = "risk"


# ---------------------------------------------------------------------------
# Stage 1: Sentiment
# ---------------------------------------------------------------------------


class TickerSentiment(BaseModel):
    ticker: str
    mentions: int = 0
    sentiment_score: float = 0.0
    top_quotes: list[str] = Field(default_factory=list)
    subreddits: dict[str, int] = Field(default_factory=dict)


class SentimentReport(BaseModel):
    tickers: list[TickerSentiment] = Field(default_factory=list)
    report_date: date | str = ""
    total_mentions: int = 0
    market_mood: str = ""


# ---------------------------------------------------------------------------
# Stage 2a: Legacy Market Analysis (no tools)
# ---------------------------------------------------------------------------


class TickerAnalysis(BaseModel):
    ticker: str
    fundamental_score: float = 0.0
    technical_score: float = 0.0
    risk_score: float = 0.0
    summary: str = ""
    catalyst: str = ""


class MarketAnalysis(BaseModel):
    tickers: list[TickerAnalysis] = Field(default_factory=list)
    market_context: str = ""


# ---------------------------------------------------------------------------
# Stage 2b: Research (with tools)
# ---------------------------------------------------------------------------


class ResearchFinding(BaseModel):
    ticker: str
    exchange: str = ""
    currency: str = "EUR"
    current_price: float = 0.0
    fundamental_score: float = 0.0
    technical_score: float = 0.0
    risk_score: float = 0.0
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    catalyst: str = ""
    earnings_outlook: str = ""
    news_summary: str = ""
    sector_peers: list[str] = Field(default_factory=list)
    summary: str = ""


class ResearchReport(BaseModel):
    tickers: list[ResearchFinding] = Field(default_factory=list)
    market_context: str = ""
    tool_calls_made: int = 0


# ---------------------------------------------------------------------------
# Stage 3: Trading Picks
# ---------------------------------------------------------------------------


class StockPick(BaseModel):
    ticker: str
    action: str = "buy"  # "buy" | "sell" | "hold"
    allocation_pct: float = 0.0
    reasoning: str = ""
    confidence: float = 0.0


class DailyPicks(BaseModel):
    llm: LLMProvider = LLMProvider.CLAUDE
    pick_date: date | None = None
    picks: list[StockPick] = Field(default_factory=list)
    sell_recommendations: list[StockPick] = Field(default_factory=list)
    confidence: float = 0.0
    market_summary: str = ""


# ---------------------------------------------------------------------------
# Stage 4: Risk Review
# ---------------------------------------------------------------------------


class PickReview(BaseModel):
    llm: LLMProvider = LLMProvider.CLAUDE
    pick_date: date | None = None
    picks: list[StockPick] = Field(default_factory=list)
    sell_recommendations: list[StockPick] = Field(default_factory=list)
    confidence: float = 0.0
    market_summary: str = ""
    risk_notes: str = ""
    adjustments: list[str] = Field(default_factory=list)
    vetoed_tickers: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Positions & Sell Signals (sourced from T212, not DB)
# ---------------------------------------------------------------------------


class Position(BaseModel):
    ticker: str
    quantity: Decimal = Decimal("0")
    avg_buy_price: Decimal = Decimal("0")
    current_price: float = 0.0
    is_real: bool = True
    llm_name: LLMProvider = LLMProvider.CLAUDE
    opened_at: date | None = None



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


class AgentStage(StrEnum):
    RESEARCH = "research"
    TRADER = "trader"


# ---------------------------------------------------------------------------
# Stage 1: Research (analyst — pros/cons/catalyst, no verdict)
# ---------------------------------------------------------------------------


class ResearchFinding(BaseModel):
    ticker: str
    exchange: str = ""
    currency: str = "EUR"
    current_price: float | None = None
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
# Stage 2: Trading Picks
# ---------------------------------------------------------------------------


class StockPick(BaseModel):
    ticker: str
    action: str = "buy"  # "buy" | "sell" | "hold"
    allocation_pct: float = 0.0
    reasoning: str = ""
    confidence: float = 0.0
    source: str = "openinsider"


class DailyPicks(BaseModel):
    llm: LLMProvider = LLMProvider.CLAUDE
    pick_date: date | None = None
    picks: list[StockPick] = Field(default_factory=list)
    sell_recommendations: list[StockPick] = Field(default_factory=list)
    confidence: float = 0.0
    market_summary: str = ""


class PickReview(BaseModel):
    llm: LLMProvider = LLMProvider.CLAUDE
    pick_date: date | None = None
    picks: list[StockPick] = Field(default_factory=list)
    sell_recommendations: list[StockPick] = Field(default_factory=list)
    confidence: float = 0.0
    market_summary: str = ""


# ---------------------------------------------------------------------------
# Positions (sourced from T212, not DB)
# ---------------------------------------------------------------------------


class Position(BaseModel):
    ticker: str
    quantity: Decimal = Decimal("0")
    avg_buy_price: Decimal = Decimal("0")
    current_price: float = 0.0
    is_real: bool = True
    llm_name: LLMProvider = LLMProvider.CLAUDE
    opened_at: date | None = None

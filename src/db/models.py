from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

# --- Enums ---


class TradeAction(StrEnum):
    BUY = "buy"
    SELL = "sell"


class TradeStatus(StrEnum):
    PENDING = "pending"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class LLMProvider(StrEnum):
    CLAUDE = "claude"
    MINIMAX = "minimax"


class AgentStage(StrEnum):
    SENTIMENT = "sentiment"
    MARKET = "market"
    TRADER = "trader"


# --- Pipeline Stage 1: Sentiment ---


class TickerSentiment(BaseModel):
    ticker: str
    mention_count: int = 0
    sentiment_score: float = Field(
        ge=-1.0, le=1.0, description="Sentiment from -1 (bearish) to 1 (bullish)"
    )
    top_quotes: list[str] = Field(
        default_factory=list, description="Notable quotes from Reddit posts"
    )
    subreddits: dict[str, int] = Field(
        default_factory=dict, description="Mention count per subreddit"
    )


class SentimentReport(BaseModel):
    report_date: date
    tickers: list[TickerSentiment]
    total_posts_analyzed: int = 0
    subreddits_scraped: list[str] = Field(default_factory=list)


# --- Pipeline Stage 2: Market Analysis ---


class TickerAnalysis(BaseModel):
    ticker: str
    exchange: str = ""
    current_price: Decimal = Decimal("0")
    currency: str = "EUR"
    fundamental_score: float = Field(
        default=0.0, ge=0.0, le=10.0, description="0-10 score on fundamentals"
    )
    technical_score: float = Field(
        default=0.0, ge=0.0, le=10.0, description="0-10 score on technical indicators"
    )
    risk_score: float = Field(default=0.0, ge=0.0, le=10.0, description="0-10 risk level")
    summary: str = ""


class MarketAnalysis(BaseModel):
    analysis_date: date
    tickers: list[TickerAnalysis]


# --- Pipeline Stage 3: Trading Decisions ---


class StockPick(BaseModel):
    ticker: str
    exchange: str = ""
    allocation_pct: float = Field(ge=0.0, le=100.0, description="% of daily budget to allocate")
    reasoning: str = ""
    action: TradeAction = TradeAction.BUY


class DailyPicks(BaseModel):
    llm: LLMProvider
    pick_date: date
    picks: list[StockPick] = Field(default_factory=list)
    sell_recommendations: list[StockPick] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    market_summary: str = ""


# --- Database Record Models ---


class Trade(BaseModel):
    id: int | None = None
    llm_name: LLMProvider
    trade_date: date
    ticker: str
    action: TradeAction
    quantity: Decimal = Decimal("0")
    price_per_share: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")
    is_real: bool = False
    broker_order_id: str | None = None
    status: TradeStatus = TradeStatus.PENDING
    created_at: datetime | None = None


class Position(BaseModel):
    id: int | None = None
    llm_name: LLMProvider
    ticker: str
    quantity: Decimal = Decimal("0")
    avg_buy_price: Decimal = Decimal("0")
    is_real: bool = False
    opened_at: datetime | None = None


class PortfolioSnapshot(BaseModel):
    id: int | None = None
    llm_name: LLMProvider
    snapshot_date: date
    total_invested: Decimal = Decimal("0")
    total_value: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    is_real: bool = False
    created_at: datetime | None = None


# --- Computed Report Models ---


class SellSignal(BaseModel):
    ticker: str
    llm_name: LLMProvider
    signal_type: str  # "stop_loss", "take_profit", "hold_period"
    trigger_price: Decimal = Decimal("0")
    position_qty: Decimal = Decimal("0")
    avg_buy_price: Decimal = Decimal("0")
    return_pct: float = 0.0
    is_real: bool = False
    reasoning: str = ""


class PnLReport(BaseModel):
    llm_name: LLMProvider
    period_start: date
    period_end: date
    total_invested: Decimal = Decimal("0")
    total_value: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    total_pnl: Decimal = Decimal("0")
    return_pct: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    is_real: bool = False

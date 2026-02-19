from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict = field(default_factory=dict)


def to_claude_tools(tools: list[ToolDef]) -> list[dict]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.parameters,
        }
        for t in tools
    ]


def to_openai_tools(tools: list[ToolDef]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


# ---------------------------------------------------------------------------
# Research Agent tools (8 read-only market data tools)
# ---------------------------------------------------------------------------

RESEARCH_TOOLS: list[ToolDef] = [
    ToolDef(
        name="get_stock_price",
        description=(
            "Get the current/recent price for a stock ticker. Returns price, currency, "
            "day change, day change %, volume, previous close. "
            "EU tickers need exchange suffix: .AS (Amsterdam), .PA (Paris), .DE (Frankfurt), "
            ".MI (Milan), .MC (Madrid), .L (London)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol, e.g. 'ASML.AS', 'SAP.DE', 'LVMH.PA'",
                },
            },
            "required": ["ticker"],
        },
    ),
    ToolDef(
        name="get_fundamentals",
        description=(
            "Get fundamental data for a stock: P/E ratio, forward P/E, EPS, market cap, "
            "dividend yield, price-to-book, revenue, profit margin, debt-to-equity, "
            "return on equity, sector, industry, and 52-week high/low."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol with exchange suffix",
                },
            },
            "required": ["ticker"],
        },
    ),
    ToolDef(
        name="get_technical_indicators",
        description=(
            "Get technical indicators for a stock: RSI (14-period), MACD (12/26/9 with histogram), "
            "Bollinger Bands (20-period, 2 std dev), and moving averages "
            "(SMA & EMA for 10, 20, 50, 200 periods). Uses 6 months of history."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol with exchange suffix",
                },
            },
            "required": ["ticker"],
        },
    ),
    ToolDef(
        name="get_stock_history",
        description=(
            "Get historical OHLCV (open/high/low/close/volume) price bars for a ticker. "
            "Useful for spotting price trends, support/resistance levels, or recent momentum."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol with exchange suffix",
                },
                "days": {
                    "type": "integer",
                    "description": (
                        "Number of days of history (default 30). "
                        "Options: 7, 14, 30, 60, 90, 180, 365."
                    ),
                    "default": 30,
                },
            },
            "required": ["ticker"],
        },
    ),
    ToolDef(
        name="get_news",
        description=(
            "Get recent news headlines and summaries for a stock ticker. "
            "Returns title, summary, provider, and publish date for each article."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol with exchange suffix",
                },
                "max_items": {
                    "type": "integer",
                    "description": "Maximum number of news articles to return (default 5)",
                    "default": 5,
                },
            },
            "required": ["ticker"],
        },
    ),
    ToolDef(
        name="get_earnings",
        description=(
            "Get upcoming earnings date and EPS estimates for a specific stock ticker. "
            "Useful for checking if a stock has an earnings event "
            "coming up that could move the price."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol with exchange suffix",
                },
            },
            "required": ["ticker"],
        },
    ),
    ToolDef(
        name="get_earnings_calendar",
        description=(
            "Get the upcoming earnings calendar for the current week across all markets. "
            "Returns ticker, company name, event type, date, and EPS estimates."
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
    ToolDef(
        name="search_eu_stocks",
        description=(
            "Search for EU-listed stocks by company name or keyword. "
            "Checks across Amsterdam (.AS), Paris (.PA), Frankfurt (.DE), "
            "Milan (.MI), Madrid (.MC), and London (.L) exchanges. "
            "Useful for discovering related companies or sector peers."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Company name or keyword to search, e.g. 'semiconductor', 'luxury', 'ASML'"
                    ),
                },
            },
            "required": ["query"],
        },
    ),
]

# ---------------------------------------------------------------------------
# Trader Agent tools (2 verification-only tools)
# ---------------------------------------------------------------------------

TRADER_TOOLS: list[ToolDef] = [
    ToolDef(
        name="get_stock_price",
        description=(
            "Verify the current price of a stock before committing to a buy/sell decision. "
            "Returns price, currency, day change %, and volume."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol with exchange suffix",
                },
            },
            "required": ["ticker"],
        },
    ),
    ToolDef(
        name="get_portfolio",
        description=(
            "Get the current portfolio positions for your LLM to check "
            "existing holdings. Returns list of positions with ticker, "
            "quantity, average buy price, and whether real/virtual."
        ),
        parameters={
            "type": "object",
            "properties": {
                "llm_name": {
                    "type": "string",
                    "description": "The LLM name to look up, either 'claude' or 'minimax'",
                },
            },
            "required": ["llm_name"],
        },
    ),
]

RESEARCH_TOOL_NAMES: set[str] = {t.name for t in RESEARCH_TOOLS}
TRADER_TOOL_NAMES: set[str] = {t.name for t in TRADER_TOOLS}

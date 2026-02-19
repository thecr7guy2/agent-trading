# Phase 8 — Agentic Tool-Calling Pipeline

## Problem

The LLM agents are **passive analyzers**. The supervisor pre-fetches all market data for all 25 candidates (75+ yfinance API calls), dumps it into prompts as massive JSON blobs, and the LLMs just score what they're given. They can't dig deeper into interesting stocks, explore related companies, verify prices before committing, or iterate on research. The LLMs are operating as glorified JSON-to-JSON transformers instead of actual research agents.

Additionally, there's no safety net between the trader's picks and execution — if the LLM is overconfident or makes a concentration error, nothing catches it.

## Goals

1. **Add LLM tool calling** so agents can pull their own market data dynamically during analysis
2. **Replace passive MarketAgent** with an active **Research Agent** that drives its own investigation using tools
3. **Add Risk Reviewer agent** as a devil's advocate / sanity check before trades execute
4. **Keep safety** — LLMs get read-only tools only, trade execution stays in supervisor
5. **Better decisions** — agents that research > agents that just analyze pre-fetched data

## What Changes From Current Architecture

| Current (Phase 7) | Phase 8 | Why |
|-|-|-|
| Supervisor calls `build_market_data()` for ALL 25 candidates (75+ API calls) | Research Agent fetches data only for tickers it finds interesting (maybe 8-10) | Less wasted API calls, more focused research |
| MarketAgent receives pre-fetched JSON blob, scores it | Research Agent uses tools to actively investigate — pulls fundamentals, technicals, news, can search for related stocks | Agentic behavior, can iterate and dig deeper |
| TraderAgent decides blind (no price verification) | TraderAgent can verify current prices via tools before allocating budget | Fewer stale-price errors |
| No review of picks before execution | Risk Reviewer checks for concentration, overconfidence, missing evidence | Catches bad trades |
| 3-stage pipeline | 4-stage pipeline | Adds research depth + risk safety |
| `generate()` only (no tools) | `generate_with_tools()` for tool-calling stages | Core capability addition |

## New Pipeline

```
Stage 1: Sentiment Agent      (Haiku)    no tools    → SentimentReport     [UNCHANGED]
Stage 2: Research Agent  [NEW] (Sonnet)   8 tools     → ResearchReport      [REPLACES MarketAgent]
Stage 3: Trader Agent          (Opus)     2 tools     → DailyPicks          [ENHANCED]
Stage 4: Risk Reviewer   [NEW] (Sonnet)   no tools    → PickReview          [NEW]
```

**Claude model assignments**: Haiku → Sonnet → Opus → Sonnet (reuses existing config fields)
**MiniMax**: Same model (MiniMax-M2.5) for all 4 stages

---

## Tool Definitions

### Research Agent Tools (8 read-only market data tools)

| Tool | Parameters | What it returns |
|------|-----------|----------------|
| `get_stock_price` | ticker | Current price, day change, volume, currency |
| `get_fundamentals` | ticker | P/E, EPS, market cap, margins, debt ratios, sector |
| `get_technical_indicators` | ticker | RSI, MACD, Bollinger Bands, SMAs/EMAs |
| `get_stock_history` | ticker, days | OHLCV price bars |
| `get_news` | ticker, max_items | Recent news headlines with summaries |
| `get_earnings` | ticker | Upcoming earnings date + EPS estimates |
| `get_earnings_calendar` | (none) | All upcoming earnings this week |
| `search_eu_stocks` | query | Search for EU stocks by name/keyword |

### Trader Agent Tools (2 verification-only tools)

| Tool | Parameters | What it returns |
|------|-----------|----------------|
| `get_stock_price` | ticker | Verify current price before allocating |
| `get_portfolio` | llm_name | Current positions to avoid doubling down |

### Safety: NO write tools exposed to LLMs
- No `place_buy_order`, `place_sell_order`, `record_virtual_trade`
- LLMs recommend, supervisor executes

---

## Tool Calling Implementation

### Provider-Agnostic Tool Schemas (`src/agents/tools.py`)

```python
@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict  # JSON Schema

def to_claude_tools(tools: list[ToolDef]) -> list[dict]:
    # Returns [{"name": ..., "description": ..., "input_schema": ...}]

def to_openai_tools(tools: list[ToolDef]) -> list[dict]:
    # Returns [{"type": "function", "function": {"name": ..., "parameters": ...}}]
```

### Tool Executor (`src/agents/tool_executor.py`)

```python
class ToolExecutor:
    def __init__(self, mcp_client: MCPToolClient, allowed_tools: set[str])

    async def execute(self, tool_name: str, args: dict) -> dict
        # Validates tool is in allowed set, calls MCP client

    async def execute_batch(self, calls: list[tuple[str, dict]]) -> list[dict]
        # Parallel execution via asyncio.gather()
```

### `generate_with_tools()` on Both Providers

Added to `ClaudeProvider` and `MiniMaxProvider` alongside existing `generate()`.

**Claude flow** (Anthropic SDK):
1. Call `messages.create(model, system, messages, tools)` with tool definitions
2. Check response for `tool_use` content blocks
3. Execute all tool calls in parallel via ToolExecutor
4. Append assistant response + tool_result messages
5. Call API again with full conversation
6. Repeat until text-only response (or max_tool_rounds hit)
7. Parse final text as JSON into output_model

**MiniMax flow** (OpenAI-compatible SDK):
1. Call `chat.completions.create(model, messages, tools)` with function definitions
2. Check response for `tool_calls` in message
3. Execute all tool calls in parallel via ToolExecutor
4. Append assistant message + tool response messages
5. Call API again
6. Repeat until no more tool_calls (or max_tool_rounds hit)
7. Parse final response as JSON into output_model

Both return `tuple[T, int]` — the parsed model + number of tool calls made (for logging).

**Max tool rounds**: Configurable, default 15. If hit, force a final response by sending without tools.

---

## New Pydantic Models (`src/db/models.py`)

```python
# Updated enum
class AgentStage(StrEnum):
    SENTIMENT = "sentiment"
    MARKET = "market"        # Keep for backward compat
    RESEARCH = "research"    # NEW
    TRADER = "trader"
    RISK = "risk"            # NEW

# Research Agent output (replaces MarketAnalysis)
class ResearchFinding(BaseModel):
    ticker: str
    exchange: str = ""
    current_price: Decimal = Decimal("0")
    currency: str = "EUR"
    fundamental_score: float      # 0-10
    technical_score: float        # 0-10
    risk_score: float             # 0-10
    news_summary: str = ""        # Key recent news
    earnings_outlook: str = ""    # Upcoming earnings context
    catalyst: str = ""            # What could move this stock
    sector_peers: list[str] = []  # Related tickers discovered
    summary: str = ""

class ResearchReport(BaseModel):
    analysis_date: date
    tickers: list[ResearchFinding]
    sectors_analyzed: list[str] = []
    tool_calls_made: int = 0
    research_notes: str = ""

# Risk Reviewer output
class PickReview(BaseModel):
    llm: LLMProvider
    pick_date: date
    picks: list[StockPick]                # Potentially modified
    sell_recommendations: list[StockPick]  # Passed through
    confidence: float                      # Potentially adjusted
    market_summary: str
    risk_notes: str = ""                   # Risk assessment
    adjustments: list[str] = []            # What was changed and why
    vetoed_tickers: list[str] = []         # Tickers removed entirely
```

---

## System Prompts

### `src/agents/prompts/research.md` (NEW)

Instructs the Research Agent to:
- Review the SentimentReport's ranked tickers
- Use tools to deeply research the **top 8-10 candidates** (not all 25)
- For each: pull fundamentals, technicals, and recent news
- Score on three dimensions: fundamental (0-10), technical (0-10), risk (0-10)
- Use `search_eu_stocks` to find related/sector-peer stocks if a sector looks promising
- Check earnings calendar for upcoming events that could move prices
- Note catalysts: upcoming earnings, positive/negative news, sector momentum
- Output a ResearchReport with detailed findings
- Be selective — quality research on fewer stocks beats shallow analysis on many

### `src/agents/prompts/risk_review.md` (NEW)

Instructs the Risk Reviewer to:
- Review each pick against the research data
- Check for **sector concentration** (>50% allocation to one sector = reduce)
- Check for **portfolio correlation** (don't double down on stocks already held)
- Flag picks with **risk_score > 7** unless exceptional catalyst
- Flag picks where **confidence seems too high** for the evidence quality
- Check that allocation percentages make sense given the research quality
- Can **reduce allocations** (never increase)
- Can **veto picks entirely** (remove from list) with documented reason
- Output PickReview — same picks list but potentially trimmed/adjusted

### `src/agents/prompts/trader.md` (UPDATED)

Add section about available verification tools:
- `get_stock_price` — verify the current price hasn't moved significantly since research
- `get_portfolio` — check what positions already exist to avoid doubling down

---

## Supervisor Changes (`src/orchestrator/supervisor.py`)

### `run_decision_cycle()`
```python
# BEFORE (Phase 7):
digest = await self.build_signal_digest()
market_data = await self.build_market_data(digest)    # 75+ API calls
pipeline_results = await self._run_pipelines(digest, market_data, run_date)

# AFTER (Phase 8):
digest = await self.build_signal_digest()              # Unchanged
# REMOVED: build_market_data() — Research Agent handles this via tools
pipeline_results = await self._run_pipelines(digest, run_date)
```

### `_run_pipelines()`
- Pass `self._market_data_client` and `self._trading_client` to `AgentPipeline`
- Pipeline uses these to create ToolExecutors for agents
- Remove `market_data` parameter
- Increase timeout from 300s to `pipeline_timeout_seconds` (default 600s)

### `_execute_real_trades()` / `_execute_virtual_trades()`
- Accept `PickReview` instead of `DailyPicks` (compatible fields)
- For price lookup: call `get_stock_price` directly (since market_data dict is no longer pre-built)

### Keep `build_market_data()` available
- Still used by `run_sell_checks()` and `run_end_of_day()` for price lookups
- Just removed from the decision cycle

---

## Config Changes (`src/config.py`)

```python
# Phase 8: Tool calling
max_tool_rounds: int = 15
pipeline_timeout_seconds: int = 600
```

---

## Files Summary

### New Files (8)
| File | Purpose |
|------|---------|
| `src/agents/tools.py` | Tool schemas + Claude/OpenAI format converters |
| `src/agents/tool_executor.py` | Executes LLM tool calls via MCP client |
| `src/agents/research_agent.py` | Research agent with tool calling |
| `src/agents/risk_agent.py` | Risk reviewer agent |
| `src/agents/prompts/research.md` | Research agent system prompt |
| `src/agents/prompts/risk_review.md` | Risk reviewer system prompt |
| `tests/test_agents/test_research_agent.py` | Research agent tests |
| `tests/test_agents/test_risk_agent.py` | Risk reviewer tests |

### Modified Files (9)
| File | Changes |
|------|---------|
| `src/agents/providers/claude.py` | Add `generate_with_tools()` with tool-calling loop |
| `src/agents/providers/minimax.py` | Add `generate_with_tools()` with tool-calling loop |
| `src/agents/pipeline.py` | 4-stage pipeline, accept MCP clients, remove market_data param |
| `src/agents/trader_agent.py` | Accept optional ToolExecutor, use tools for verification |
| `src/agents/prompts/trader.md` | Add tool usage instructions |
| `src/db/models.py` | Add ResearchFinding, ResearchReport, PickReview, new AgentStage values |
| `src/orchestrator/supervisor.py` | Remove build_market_data from cycle, pass clients to pipeline, handle PickReview |
| `src/config.py` | Add max_tool_rounds, pipeline_timeout_seconds |
| `tests/test_agents/test_pipeline.py` | Update for 4-stage flow |

### Test Fixture Updates (existing files)
- `tests/test_orchestrator/test_supervisor.py` — update for removed market_data, PickReview output
- `tests/test_orchestrator/test_scheduler.py` — add new config fields to SimpleNamespace
- `tests/test_orchestrator/test_signal_digest.py` — add new config fields to SimpleNamespace

---

## Implementation Order

1. Tool schemas (`src/agents/tools.py`)
2. Tool executor (`src/agents/tool_executor.py`)
3. New Pydantic models (`src/db/models.py`)
4. Claude `generate_with_tools()` (`src/agents/providers/claude.py`)
5. MiniMax `generate_with_tools()` (`src/agents/providers/minimax.py`)
6. System prompts (`research.md`, `risk_review.md`)
7. Research agent (`src/agents/research_agent.py`)
8. Risk reviewer agent (`src/agents/risk_agent.py`)
9. Enhance trader agent (`src/agents/trader_agent.py` + `trader.md`)
10. Update pipeline (`src/agents/pipeline.py`)
11. Config changes (`src/config.py`)
12. Supervisor changes (`src/orchestrator/supervisor.py`)
13. All tests
14. Lint + format

---

## Verification

1. `uv run ruff check src/ --fix && uv run ruff format src/`
2. `uv run pytest tests/ -v` — full suite passes, no regressions
3. `uv run python scripts/run_daily.py --no-approval` — manual run, check logs for tool calls
4. Verify Research Agent logs show tool calls (ticker + tool name + result summary)
5. Verify PickReview output includes risk_notes and any adjustments
6. Verify total pipeline time is under 600s (tool calling adds latency)

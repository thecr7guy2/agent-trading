# Phase 3: LLM Agents Implementation Plan

## Context

Phase 1 (Foundation) and Phase 2 (MCP Servers) are complete. Phase 3 builds the LLM agent pipeline: two provider wrappers (Claude + MiniMax), three agent stages (Sentiment → Market → Trader), system prompts, and a pipeline orchestrator that runs all three stages in sequence.

## Step 1: Add Dependencies

**File:** `pyproject.toml`

Add to `dependencies`:
```
"anthropic>=0.52",
"openai>=1.60",
```

Run `uv sync` after.

## Step 2: Provider Wrappers

### `src/agents/providers/claude.py`

Wraps the Anthropic SDK (`AsyncAnthropic`) for structured output:

```python
class ClaudeProvider:
    def __init__(self, api_key: str):
        self._client = AsyncAnthropic(api_key=api_key)

    async def generate(self, model: str, system_prompt: str,
                       user_message: str, output_model: type[T],
                       max_tokens: int = 2048) -> T:
        # Uses client.messages.create() with JSON prompt instructions
        # Parses response with output_model.model_validate_json()
        # Falls back gracefully if structured output isn't available
```

- Uses `AsyncAnthropic` for async calls
- Model selection: Haiku (sentiment), Sonnet (market), Opus (trader) — passed in by the agent
- Structured output: Ask for JSON in the system prompt, parse with Pydantic `model_validate_json()`
- This approach is more robust than `.parse()` since it works reliably across all Claude models

### `src/agents/providers/minimax.py`

Wraps the OpenAI SDK (`AsyncOpenAI`) with custom base_url for MiniMax:

```python
class MiniMaxProvider:
    def __init__(self, api_key: str, base_url: str):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def generate(self, model: str, system_prompt: str,
                       user_message: str, output_model: type[T],
                       max_tokens: int = 2048) -> T:
        # Uses client.chat.completions.create() with JSON prompt instructions
        # Parses response with output_model.model_validate_json()
```

- Uses `AsyncOpenAI(base_url=minimax_base_url)` pointing to MiniMax
- Same `generate()` interface as ClaudeProvider for interchangeability
- JSON extraction via prompt + Pydantic validation (most reliable across providers)

### `src/agents/providers/__init__.py`

Export a factory function:
```python
def get_provider(llm: LLMProvider) -> ClaudeProvider | MiniMaxProvider
```

Both providers share the same interface: `generate(model, system_prompt, user_message, output_model) -> T`

## Step 3: System Prompts

Markdown files in `src/agents/prompts/`. Each prompt tells the LLM its role, what input it receives, and the exact JSON schema it must return.

### `src/agents/prompts/sentiment.md`
- Role: Reddit sentiment analyst for European stocks
- Input: Raw Reddit digest data (posts, tickers, upvotes, subreddits)
- Task: Score each ticker's sentiment [-1, 1], rank by conviction, extract notable quotes
- Output: JSON matching `SentimentReport` schema (report_date, tickers[], total_posts_analyzed, subreddits_scraped)
- Guidelines: Focus on EU stocks, filter noise, weight by upvotes and subreddit quality

### `src/agents/prompts/market_analysis.md`
- Role: Market analyst for European equities
- Input: Sentiment report + market data (prices, fundamentals, technicals per ticker)
- Task: Score each ticker on fundamentals (0-10), technicals (0-10), risk (0-10), write summary
- Output: JSON matching `MarketAnalysis` schema (analysis_date, tickers[])
- Guidelines: Consider P/E, market cap, RSI, MACD, Bollinger position, moving average crossovers

### `src/agents/prompts/trader.md`
- Role: Portfolio trader with 10 EUR daily budget
- Input: Sentiment report + market analysis + current portfolio + budget
- Task: Pick up to 3 stocks to buy (with allocation %), recommend sells for existing positions
- Output: JSON matching `DailyPicks` schema (llm, pick_date, picks[], sell_recommendations[], confidence, market_summary)
- Guidelines: Conservative position sizing, diversification, consider existing exposure, allocations must sum to ≤100%

## Step 4: Agent Implementations

All three agents inherit from `BaseAgent` and follow the same pattern:
1. Load system prompt from markdown file
2. Format user message with input data
3. Call provider's `generate()` method
4. Return typed Pydantic model

### `src/agents/sentiment_agent.py`
```python
class SentimentAgent(BaseAgent):
    def __init__(self, provider: ClaudeProvider | MiniMaxProvider,
                 model: str, llm: LLMProvider):
        ...
    async def run(self, input_data: dict) -> SentimentReport:
        # input_data = raw Reddit digest from MCP server
        # Formats digest into a user message
        # Calls provider.generate(..., output_model=SentimentReport)
```

### `src/agents/market_agent.py`
```python
class MarketAgent(BaseAgent):
    def __init__(self, provider, model, llm):
        ...
    async def run(self, input_data: dict) -> MarketAnalysis:
        # input_data = {"sentiment": SentimentReport, "market_data": dict}
        # Formats both into a user message
        # Calls provider.generate(..., output_model=MarketAnalysis)
```

### `src/agents/trader_agent.py`
```python
class TraderAgent(BaseAgent):
    def __init__(self, provider, model, llm):
        ...
    async def run(self, input_data: dict) -> DailyPicks:
        # input_data = {"sentiment": SentimentReport, "market_analysis": MarketAnalysis,
        #               "portfolio": list, "budget_eur": float}
        # Formats everything into a user message
        # Calls provider.generate(..., output_model=DailyPicks)
```

## Step 5: Pipeline

### `src/agents/pipeline.py`

Runs all 3 stages in sequence for a given LLM provider:

```python
class AgentPipeline:
    def __init__(self, llm: LLMProvider):
        # Creates provider + 3 agents with correct models
        # Claude: Haiku → Sonnet → Opus
        # MiniMax: MiniMax-Text-01 for all 3

    async def run(self, reddit_digest: dict, market_data: dict,
                  portfolio: list, budget_eur: float) -> DailyPicks:
        # Stage 1: sentiment = await sentiment_agent.run(reddit_digest)
        # Stage 2: analysis = await market_agent.run({sentiment, market_data})
        # Stage 3: picks = await trader_agent.run({sentiment, analysis, portfolio, budget})
        # return picks
```

- Provider-agnostic: pass `LLMProvider.CLAUDE` or `LLMProvider.MINIMAX`
- The pipeline does NOT call MCP servers — it receives pre-fetched data as arguments
- The orchestrator (Phase 4) handles data fetching and passing it to the pipeline

## Step 6: Tests

### `tests/test_agents/test_providers.py`
- Test ClaudeProvider with mocked `anthropic.AsyncAnthropic`
- Test MiniMaxProvider with mocked `openai.AsyncOpenAI`
- Test JSON parsing + Pydantic validation
- Test fallback when LLM returns malformed JSON

### `tests/test_agents/test_sentiment_agent.py`
- Test with mocked provider returning valid SentimentReport JSON
- Test prompt loading from markdown file
- Test input formatting

### `tests/test_agents/test_market_agent.py`
- Test with mocked provider returning valid MarketAnalysis JSON
- Test that sentiment + market data are both included in prompt

### `tests/test_agents/test_trader_agent.py`
- Test with mocked provider returning valid DailyPicks JSON
- Test allocation_pct validation (sums to ≤100%)
- Test portfolio context is included in prompt

### `tests/test_agents/test_pipeline.py`
- Test full pipeline with all 3 agents mocked
- Test pipeline for both Claude and MiniMax providers
- Verify data flows correctly between stages

## Implementation Order

1. `pyproject.toml` — add `anthropic`, `openai` deps → `uv sync`
2. `src/agents/providers/claude.py` — Claude provider wrapper
3. `src/agents/providers/minimax.py` — MiniMax provider wrapper
4. `src/agents/providers/__init__.py` — factory function
5. `src/agents/prompts/sentiment.md` — Stage 1 system prompt
6. `src/agents/prompts/market_analysis.md` — Stage 2 system prompt
7. `src/agents/prompts/trader.md` — Stage 3 system prompt
8. `src/agents/sentiment_agent.py` — Stage 1 agent
9. `src/agents/market_agent.py` — Stage 2 agent
10. `src/agents/trader_agent.py` — Stage 3 agent
11. `src/agents/pipeline.py` — Pipeline orchestrator
12. Tests for all of the above

## Key Design Decisions

- **JSON via prompt, not SDK structured output**: Both providers ask the LLM for JSON in the system prompt and parse with `model_validate_json()`. This is more portable than provider-specific structured output features which may not work on MiniMax.
- **Providers share the same interface**: `generate(model, system_prompt, user_message, output_model) -> T` so agents are provider-agnostic.
- **Agents don't call MCP servers**: They receive data and return analysis. The orchestrator (Phase 4) handles I/O.
- **Prompts in markdown files**: Easy to iterate on without code changes.
- **Pipeline is provider-agnostic**: Just pass `LLMProvider.CLAUDE` or `LLMProvider.MINIMAX`.

## Verification

1. `uv run ruff check src/agents/ --fix && uv run ruff format src/agents/`
2. `uv run pytest tests/ -v` — all tests pass
3. Verify prompt files exist and are well-formatted

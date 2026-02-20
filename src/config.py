from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM APIs
    anthropic_api_key: str
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimax.io/v1"

    # Reddit (optional — RSS feeds don't require API credentials)
    reddit_client_id: str | None = None
    reddit_client_secret: str | None = None
    reddit_user_agent: str = "trading-bot/1.0"

    # Broker — Live (real money)
    t212_api_key: str
    t212_api_secret: str

    # Broker — Practice / Demo (T212 demo account)
    t212_practice_api_key: str | None = None
    t212_practice_api_secret: str | None = None
    practice_daily_budget_eur: float = 500.0

    # Database
    database_url: str = "postgresql://postgres:postgres@localhost:5432/trading_bot"

    # Trading
    daily_budget_eur: float = 10.0
    market_data_ticker_limit: int = 12

    # Orchestration
    orchestrator_timezone: str = "Europe/Berlin"
    approval_timeout_seconds: int = 120
    approval_timeout_action: str = "approve_all"
    scheduler_collect_times: str = "08:00,12:00,16:30"
    scheduler_execute_time: str = "17:10"
    scheduler_eod_time: str = "17:35"

    # Optional: Telegram
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_enabled: bool = False

    # Sell automation
    sell_stop_loss_pct: float = 10.0
    sell_take_profit_pct: float = 15.0
    sell_max_hold_days: int = 5
    sell_check_schedule: str = "09:30,12:30,16:45"

    # Backtesting
    backtest_daily_budget_eur: float = 10.0

    # Claude model IDs
    claude_haiku_model: str = "claude-haiku-4-5-20251001"
    claude_sonnet_model: str = "claude-sonnet-4-6"
    claude_opus_model: str = "claude-opus-4-6"

    # Multi-source signals (Phase 7)
    signal_candidate_limit: int = 25
    screener_min_market_cap: int = 1_000_000_000
    screener_exchanges: str = "AMS,PAR,GER,MIL,MCE,LSE"

    # BAFIN insider trades
    bafin_lookback_days: int = 7

    # MiniMax model ID (kept for backward compat, unused in main pipeline)
    minimax_model: str = "MiniMax-M2.5"

    # Phase 8: Tool calling
    max_tool_rounds: int = 10
    pipeline_timeout_seconds: int = 900


def get_settings() -> Settings:
    return Settings()

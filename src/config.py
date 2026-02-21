from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM APIs
    anthropic_api_key: str
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimax.io/v1"

    # Data sources (all optional — bot degrades gracefully if missing)
    news_api_key: str = ""
    fmp_api_key: str = ""

    # Reddit (RSS feeds don't require credentials, but kept for future use)
    reddit_user_agent: str = "trading-bot/1.0"

    # Broker — Live (real money)
    t212_api_key: str
    t212_api_secret: str = ""

    # Broker — Practice / Demo
    t212_practice_api_key: str | None = None
    t212_practice_api_secret: str | None = None
    practice_daily_budget_eur: float = 500.0

    # Trading
    daily_budget_eur: float = 10.0
    max_candidates: int = 15

    # Stock variety — rolling blacklist
    recently_traded_path: str = "recently_traded.json"
    recently_traded_days: int = 3

    # EU soft preference — scoring bonus for EU-listed tickers (0.1 = 10% boost)
    # No hard exclusion — best global pick always wins, EU gets a nudge when quality is equal
    eu_preference_bonus: float = 0.1

    # Orchestration
    orchestrator_timezone: str = "Europe/Berlin"
    scheduler_collect_times: str = "08:00,12:00,16:30"
    scheduler_execute_time: str = "17:10"
    scheduler_eod_time: str = "17:35"

    # Telegram (optional)
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_enabled: bool = False

    # Sell automation
    sell_stop_loss_pct: float = 10.0
    sell_take_profit_pct: float = 15.0
    sell_max_hold_days: int = 5
    sell_check_schedule: str = "09:30,12:30,16:45"

    # Claude model IDs
    claude_haiku_model: str = "claude-haiku-4-5-20251001"
    claude_sonnet_model: str = "claude-sonnet-4-6"
    claude_opus_model: str = "claude-opus-4-6"

    # MiniMax model ID
    minimax_model: str = "MiniMax-M2.5"

    # Pipeline
    max_tool_rounds: int = 10
    pipeline_timeout_seconds: int = 900


def get_settings() -> Settings:
    return Settings()

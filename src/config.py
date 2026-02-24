from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM APIs
    anthropic_api_key: str

    # Data sources (all optional — bot degrades gracefully if missing)
    news_api_key: str = ""
    fmp_api_key: str = ""

    # Broker — Practice / Demo only (single account)
    t212_api_key: str
    t212_api_secret: str = ""

    # Budget
    budget_per_run_eur: float = 1000.0
    max_picks_per_run: int = 5

    # Stock variety — rolling blacklist
    recently_traded_path: str = "recently_traded.json"
    recently_traded_days: int = 3

    # Insider pipeline
    insider_lookback_days: int = 5
    min_insider_tickers: int = 10
    insider_top_n: int = 25
    research_top_n: int = 15  # max candidates passed to research stage

    # Orchestration
    orchestrator_timezone: str = "Europe/Berlin"
    scheduler_execute_time: str = "17:10"
    scheduler_eod_time: str = "17:35"
    scheduler_trade_days: str = "tue,fri"  # APScheduler day_of_week format

    # Telegram (optional)
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_enabled: bool = False

    # Claude model IDs
    claude_haiku_model: str = "claude-haiku-4-5-20251001"
    claude_sonnet_model: str = "claude-sonnet-4-6"
    claude_opus_model: str = "claude-opus-4-6"

    # Pipeline
    max_tool_rounds: int = 10
    pipeline_timeout_seconds: int = 900


    @model_validator(mode="after")
    def validate_credentials(self) -> "Settings":
        if not self.t212_api_key.strip():
            raise ValueError(
                "T212_API_KEY is set but empty — set a valid Trading 212 API key in .env"
            )
        return self


def get_settings() -> Settings:
    return Settings()

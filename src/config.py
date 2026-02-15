from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM APIs
    anthropic_api_key: str
    minimax_api_key: str
    minimax_base_url: str = "https://api.minimaxi.chat/v1"

    # Reddit
    reddit_client_id: str
    reddit_client_secret: str
    reddit_user_agent: str = "trading-bot/1.0"

    # Broker
    t212_api_key: str

    # Database
    database_url: str = "postgresql://postgres:postgres@localhost:5432/trading_bot"

    # Trading
    daily_budget_eur: float = 10.0

    # Optional: Telegram
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    # Claude model IDs
    claude_haiku_model: str = "claude-haiku-4-5-20251001"
    claude_sonnet_model: str = "claude-sonnet-4-5-20250929"
    claude_opus_model: str = "claude-opus-4-6"

    # MiniMax model ID
    minimax_model: str = Field(default="MiniMax-Text-01")


def get_settings() -> Settings:
    return Settings()

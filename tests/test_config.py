import pytest

from src.config import Settings


class TestSettings:
    def test_loads_from_env_vars(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("MINIMAX_API_KEY", "mm-test")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "reddit-id")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "reddit-secret")
        monkeypatch.setenv("T212_API_KEY", "t212-test")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")

        settings = Settings()

        assert settings.anthropic_api_key == "sk-ant-test"
        assert settings.minimax_api_key == "mm-test"
        assert settings.t212_api_key == "t212-test"
        assert settings.database_url == "postgresql://test:test@localhost/test"

    def test_defaults(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        monkeypatch.setenv("MINIMAX_API_KEY", "x")
        monkeypatch.setenv("REDDIT_CLIENT_ID", "x")
        monkeypatch.setenv("REDDIT_CLIENT_SECRET", "x")
        monkeypatch.setenv("T212_API_KEY", "x")

        settings = Settings()

        assert settings.daily_budget_eur == 10.0
        assert settings.reddit_user_agent == "trading-bot/1.0"
        assert settings.telegram_bot_token is None
        assert settings.telegram_chat_id is None
        assert settings.claude_opus_model == "claude-opus-4-6"
        assert settings.claude_sonnet_model == "claude-sonnet-4-5-20250929"
        assert settings.claude_haiku_model == "claude-haiku-4-5-20251001"

    def test_missing_required_key_raises(self, monkeypatch):
        # Clear all env vars that could satisfy required fields
        for key in [
            "ANTHROPIC_API_KEY",
            "MINIMAX_API_KEY",
            "REDDIT_CLIENT_ID",
            "REDDIT_CLIENT_SECRET",
            "T212_API_KEY",
        ]:
            monkeypatch.delenv(key, raising=False)

        with pytest.raises(Exception):
            Settings(_env_file=None)

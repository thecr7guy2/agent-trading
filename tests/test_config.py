import pytest

from src.config import Settings


class TestSettings:
    def test_loads_from_env_vars(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("MINIMAX_API_KEY", "mm-test")
        monkeypatch.setenv("T212_API_KEY", "t212-test")
        monkeypatch.setenv("T212_API_SECRET", "t212-secret-test")
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")

        settings = Settings()

        assert settings.anthropic_api_key == "sk-ant-test"
        assert settings.minimax_api_key == "mm-test"
        assert settings.t212_api_key == "t212-test"
        assert settings.t212_api_secret == "t212-secret-test"
        assert settings.database_url == "postgresql://test:test@localhost/test"

    def test_defaults(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        monkeypatch.setenv("MINIMAX_API_KEY", "x")
        monkeypatch.setenv("T212_API_KEY", "x")
        monkeypatch.setenv("T212_API_SECRET", "x")

        settings = Settings(_env_file=None)

        assert settings.daily_budget_eur == 10.0
        assert settings.reddit_user_agent == "trading-bot/1.0"
        assert settings.telegram_bot_token is None
        assert settings.telegram_chat_id is None
        assert settings.claude_opus_model == "claude-opus-4-6"
        assert settings.claude_sonnet_model == "claude-sonnet-4-5-20250929"
        assert settings.claude_haiku_model == "claude-haiku-4-5-20251001"
        assert settings.orchestrator_timezone == "Europe/Berlin"
        assert settings.approval_timeout_seconds == 120
        assert settings.approval_timeout_action == "approve_all"
        assert settings.max_tool_rounds == 8
        assert settings.pipeline_timeout_seconds == 900

    def test_reddit_credentials_optional(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        monkeypatch.setenv("MINIMAX_API_KEY", "x")
        monkeypatch.setenv("T212_API_KEY", "x")
        monkeypatch.setenv("T212_API_SECRET", "x")

        settings = Settings()

        assert settings.reddit_client_id is None
        assert settings.reddit_client_secret is None

    def test_missing_required_key_raises(self, monkeypatch):
        for key in [
            "ANTHROPIC_API_KEY",
            "MINIMAX_API_KEY",
            "T212_API_KEY",
        ]:
            monkeypatch.delenv(key, raising=False)

        with pytest.raises(Exception):
            Settings(_env_file=None)

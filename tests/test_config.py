import pytest

from src.config import Settings


class TestSettings:
    def test_loads_required_keys_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("T212_API_KEY", "t212-test")

        settings = Settings(_env_file=None)

        assert settings.anthropic_api_key == "sk-ant-test"
        assert settings.t212_api_key == "t212-test"

    def test_defaults(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        monkeypatch.setenv("T212_API_KEY", "x")

        settings = Settings(_env_file=None)

        assert settings.budget_per_run_eur == 1000.0
        assert settings.max_picks_per_run == 5
        assert settings.insider_top_n == 25
        assert settings.research_top_n == 15
        assert settings.recently_traded_days == 3
        assert settings.claude_sonnet_model == "claude-sonnet-4-6"
        assert settings.claude_opus_model == "claude-opus-4-6"
        assert settings.claude_haiku_model == "claude-haiku-4-5-20251001"
        assert settings.orchestrator_timezone == "Europe/Berlin"
        assert settings.max_tool_rounds == 10
        assert settings.pipeline_timeout_seconds == 900
        assert settings.telegram_bot_token is None
        assert settings.telegram_enabled is False

    def test_missing_required_key_raises(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("T212_API_KEY", raising=False)

        with pytest.raises(Exception):
            Settings(_env_file=None)

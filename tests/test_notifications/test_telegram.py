import pytest

from src.config import Settings
from src.notifications.telegram import TelegramNotifier


@pytest.fixture
def disabled_settings():
    return Settings(
        anthropic_api_key="test",
        minimax_api_key="test",
        t212_api_key="test",
        t212_api_secret="test",
        telegram_enabled=False,
        telegram_bot_token=None,
        telegram_chat_id=None,
    )


@pytest.fixture
def enabled_settings():
    return Settings(
        anthropic_api_key="test",
        minimax_api_key="test",
        t212_api_key="test",
        t212_api_secret="test",
        telegram_enabled=True,
        telegram_bot_token="fake-token",
        telegram_chat_id="12345",
    )


class TestTelegramNotifier:
    def test_disabled_when_flag_false(self, disabled_settings):
        notifier = TelegramNotifier(disabled_settings)
        assert not notifier.enabled

    def test_disabled_when_token_missing(self):
        settings = Settings(
            anthropic_api_key="test",
            minimax_api_key="test",
            t212_api_key="test",
        t212_api_secret="test",
            telegram_enabled=True,
            telegram_bot_token=None,
            telegram_chat_id="12345",
        )
        notifier = TelegramNotifier(settings)
        assert not notifier.enabled

    def test_enabled_when_configured(self, enabled_settings):
        notifier = TelegramNotifier(enabled_settings)
        assert notifier.enabled

    @pytest.mark.asyncio
    async def test_send_message_noop_when_disabled(self, disabled_settings):
        notifier = TelegramNotifier(disabled_settings)
        result = await notifier.send_message("test")
        assert result["status"] == "skipped"
        assert result["reason"] == "telegram_disabled"

    @pytest.mark.asyncio
    async def test_notify_daily_summary_noop_when_disabled(self, disabled_settings):
        notifier = TelegramNotifier(disabled_settings)
        result = await notifier.notify_daily_summary(
            {
                "main_trader": "claude",
                "virtual_trader": "minimax",
                "date": "2025-02-15",
                "real_execution": [{"ticker": "ASML.AS"}],
                "virtual_execution": [],
            }
        )
        assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_notify_sell_signals_noop_no_sells(self, disabled_settings):
        notifier = TelegramNotifier(disabled_settings)
        result = await notifier.notify_sell_signals({"executed_sells": []})
        assert result["status"] == "skipped"
        assert result["reason"] == "no_sells"

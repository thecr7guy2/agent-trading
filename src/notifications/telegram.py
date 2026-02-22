import logging

import httpx

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramNotifier:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        return (
            self._settings.telegram_enabled
            and bool(self._settings.telegram_bot_token)
            and bool(self._settings.telegram_chat_id)
        )

    async def send_message(self, text: str) -> dict:
        if not self.enabled:
            return {"status": "skipped", "reason": "telegram_disabled"}

        url = f"{TELEGRAM_API_BASE}/bot{self._settings.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self._settings.telegram_chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                return {"status": "sent", "response": resp.json()}
        except Exception:
            logger.exception("Failed to send Telegram message")
            return {"status": "error", "reason": "send_failed"}

    async def notify_daily_summary(self, decision_result: dict) -> dict:
        run_date = decision_result.get("date", "?")
        insider_count = decision_result.get("insider_count", 0)
        execution = decision_result.get("execution", [])
        filled = [e for e in execution if e.get("status") == "filled"]
        failed = [e for e in execution if e.get("status") == "failed"]
        picks = decision_result.get("picks", [])
        confidence = decision_result.get("confidence", 0.0)
        market_summary = decision_result.get("market_summary", "")

        lines = [
            f"*Daily Summary - {run_date}*",
            f"Insider candidates: {insider_count}",
            f"Picks: {len(picks)} | Filled: {len(filled)} | Failed: {len(failed)}",
            f"Confidence: {confidence:.2f}",
        ]
        if market_summary:
            lines.append(f"_{market_summary[:200]}_")
        if filled:
            tickers = ", ".join(e.get("ticker", "?") for e in filled)
            lines.append(f"Bought: {tickers}")

        return await self.send_message("\n".join(lines))

    async def notify_sell_signals(self, sell_result: dict) -> dict:
        sells = sell_result.get("executed_sells", [])
        if not sells:
            return {"status": "skipped", "reason": "no_sells"}

        run_date = sell_result.get("date", "?")
        lines = [f"*Sell Triggers - {run_date}*"]
        for sell in sells:
            ticker = sell.get("ticker", "?")
            reason = sell.get("reasoning", "")
            pnl = sell.get("return_pct", 0)
            pnl_str = f"+{pnl:.1f}%" if pnl >= 0 else f"{pnl:.1f}%"
            lines.append(f"  {ticker}: {reason} ({pnl_str})")

        return await self.send_message("\n".join(lines))

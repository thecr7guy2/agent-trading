import logging
from datetime import date, datetime
from decimal import Decimal

from src.config import Settings, get_settings
from src.models import Position, SellSignal

logger = logging.getLogger(__name__)


class SellStrategyEngine:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    def evaluate_position(
        self,
        position: Position,
        current_price: float,
        today: date | None = None,
    ) -> SellSignal | None:
        today = today or date.today()
        if current_price <= 0 or position.quantity <= 0:
            return None

        avg_buy = float(position.avg_buy_price)
        if avg_buy <= 0:
            return None

        return_pct = ((current_price - avg_buy) / avg_buy) * 100

        # Stop-loss
        if return_pct <= -self._settings.sell_stop_loss_pct:
            return SellSignal(
                ticker=position.ticker,
                llm_name=position.llm_name,
                signal_type="stop_loss",
                trigger_price=Decimal(str(current_price)),
                position_qty=position.quantity,
                avg_buy_price=position.avg_buy_price,
                return_pct=round(return_pct, 2),
                is_real=position.is_real,
                reasoning=(
                    f"Stop-loss: {return_pct:.1f}%"
                    f" (threshold: -{self._settings.sell_stop_loss_pct}%)"
                ),
            )

        # Take-profit
        if return_pct >= self._settings.sell_take_profit_pct:
            return SellSignal(
                ticker=position.ticker,
                llm_name=position.llm_name,
                signal_type="take_profit",
                trigger_price=Decimal(str(current_price)),
                position_qty=position.quantity,
                avg_buy_price=position.avg_buy_price,
                return_pct=round(return_pct, 2),
                is_real=position.is_real,
                reasoning=(
                    f"Take-profit: +{return_pct:.1f}%"
                    f" (threshold: +{self._settings.sell_take_profit_pct}%)"
                ),
            )

        # Hold-period
        if position.opened_at:
            opened_date = (
                position.opened_at.date()
                if isinstance(position.opened_at, datetime)
                else position.opened_at
            )
            days_held = (today - opened_date).days
            if days_held >= self._settings.sell_max_hold_days:
                return SellSignal(
                    ticker=position.ticker,
                    llm_name=position.llm_name,
                    signal_type="hold_period",
                    trigger_price=Decimal(str(current_price)),
                    position_qty=position.quantity,
                    avg_buy_price=position.avg_buy_price,
                    return_pct=round(return_pct, 2),
                    is_real=position.is_real,
                    reasoning=(
                        f"Hold-period: {days_held} days (max: {self._settings.sell_max_hold_days})"
                    ),
                )

        return None

    def evaluate_positions(
        self,
        positions: list[Position],
        prices: dict[str, float],
        today: date | None = None,
    ) -> list[SellSignal]:
        signals: list[SellSignal] = []
        for position in positions:
            price = prices.get(position.ticker, 0.0)
            signal = self.evaluate_position(position, price, today)
            if signal is not None:
                signals.append(signal)
        return signals

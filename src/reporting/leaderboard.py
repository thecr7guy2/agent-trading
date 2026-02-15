from datetime import date

from src.db.models import LLMProvider
from src.reporting.pnl import PnLEngine


class LeaderboardBuilder:
    def __init__(self, pnl_engine: PnLEngine):
        self._pnl = pnl_engine

    async def build(self, start_date: date, end_date: date) -> list[dict]:
        entries: list[dict] = []

        for llm in LLMProvider:
            # Combine real + virtual for overall ranking
            real_report = await self._pnl.get_pnl_report(llm, start_date, end_date, is_real=True)
            virtual_report = await self._pnl.get_pnl_report(
                llm, start_date, end_date, is_real=False
            )

            total_pnl = real_report.total_pnl + virtual_report.total_pnl
            total_invested = real_report.total_invested + virtual_report.total_invested
            total_wins = real_report.win_count + virtual_report.win_count
            total_losses = real_report.loss_count + virtual_report.loss_count
            total_trades = total_wins + total_losses
            win_rate = round(total_wins / total_trades, 2) if total_trades > 0 else 0.0
            avg_return = (
                round(float(total_pnl / total_invested * 100), 2) if total_invested else 0.0
            )

            entries.append(
                {
                    "llm_name": llm.value,
                    "pnl": str(total_pnl),
                    "win_rate": win_rate,
                    "avg_return": avg_return,
                    "total_trades": total_trades,
                }
            )

        entries.sort(key=lambda e: float(e["pnl"]), reverse=True)
        for rank, entry in enumerate(entries, start=1):
            entry["rank"] = rank

        return entries

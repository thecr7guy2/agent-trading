from datetime import date
from decimal import Decimal

from src.db.models import LLMProvider, PnLReport
from src.mcp_servers.trading.portfolio import PortfolioManager
from src.orchestrator.mcp_client import MCPToolClient


class PnLEngine:
    def __init__(self, pm: PortfolioManager, market_client: MCPToolClient):
        self._pm = pm
        self._market_client = market_client

    async def get_pnl_report(
        self,
        llm_name: LLMProvider,
        start_date: date,
        end_date: date,
        is_real: bool = True,
    ) -> PnLReport:
        pnl_data = await self._pm.calculate_pnl(llm_name.value, start_date, end_date)

        total_invested = Decimal(pnl_data["total_invested"])
        realized_pnl = Decimal(pnl_data["realized_pnl"])
        win_count = pnl_data["win_count"]
        loss_count = pnl_data["loss_count"]
        total_trades = win_count + loss_count

        # Calculate unrealized P&L from current positions
        positions = await self._pm.get_positions_typed(llm_name.value)
        filtered = [p for p in positions if p.is_real == is_real]

        unrealized_pnl = Decimal("0")
        current_value = Decimal("0")
        position_invested = Decimal("0")

        for pos in filtered:
            invested = pos.quantity * pos.avg_buy_price
            position_invested += invested
            price_resp = await self._market_client.call_tool(
                "get_stock_price", {"ticker": pos.ticker}
            )
            current_price = self._extract_price(price_resp)
            if current_price > 0:
                value = pos.quantity * Decimal(str(current_price))
            else:
                value = invested
            current_value += value
            unrealized_pnl += value - invested

        total_pnl = realized_pnl + unrealized_pnl
        # Use position_invested for total_value context; total_invested is from trades in period
        total_value = current_value if filtered else total_invested + realized_pnl
        return_pct = float(total_pnl / total_invested * 100) if total_invested else 0.0

        return PnLReport(
            llm_name=llm_name,
            period_start=start_date,
            period_end=end_date,
            total_invested=total_invested,
            total_value=total_value,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            total_pnl=total_pnl,
            return_pct=round(return_pct, 2),
            win_count=win_count,
            loss_count=loss_count,
            win_rate=round(win_count / total_trades, 2) if total_trades > 0 else 0.0,
            is_real=is_real,
        )

    async def get_best_worst_picks(self, start_date: date, end_date: date) -> dict:
        all_picks: list[dict] = []

        for llm in LLMProvider:
            trades = await self._pm.get_trade_history(llm.value, limit=200)
            buy_trades = [
                t
                for t in trades
                if t["action"] == "buy"
                and start_date <= date.fromisoformat(t["trade_date"]) <= end_date
            ]
            for trade in buy_trades:
                price_resp = await self._market_client.call_tool(
                    "get_stock_price", {"ticker": trade["ticker"]}
                )
                current_price = self._extract_price(price_resp)
                entry_price = float(trade["price_per_share"])
                if entry_price <= 0:
                    continue
                return_pct = ((current_price - entry_price) / entry_price) * 100
                all_picks.append(
                    {
                        "ticker": trade["ticker"],
                        "return_pct": round(return_pct, 2),
                        "llm": llm.value,
                        "date": trade["trade_date"],
                    }
                )

        if not all_picks:
            return {"best": None, "worst": None}

        best = max(all_picks, key=lambda x: x["return_pct"])
        worst = min(all_picks, key=lambda x: x["return_pct"])
        return {"best": best, "worst": worst}

    async def get_portfolio_summary(self, is_real: bool = True) -> dict:
        total_invested = Decimal("0")
        total_value = Decimal("0")

        for llm in LLMProvider:
            positions = await self._pm.get_positions_typed(llm.value)
            filtered = [p for p in positions if p.is_real == is_real]
            for pos in filtered:
                invested = pos.quantity * pos.avg_buy_price
                total_invested += invested
                price_resp = await self._market_client.call_tool(
                    "get_stock_price", {"ticker": pos.ticker}
                )
                current_price = self._extract_price(price_resp)
                if current_price > 0:
                    total_value += pos.quantity * Decimal(str(current_price))
                else:
                    total_value += invested

        pnl = total_value - total_invested
        return_pct = float(pnl / total_invested * 100) if total_invested else 0.0
        return {
            "total_invested": str(total_invested),
            "total_value": str(total_value),
            "pnl": str(pnl),
            "return_pct": round(return_pct, 2),
        }

    @staticmethod
    def _extract_price(price_payload: dict) -> float:
        if not isinstance(price_payload, dict):
            return 0.0
        val = price_payload.get("price")
        if val is None:
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

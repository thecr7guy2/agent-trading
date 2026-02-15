import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from src.db.models import DailyPicks


@dataclass
class ApprovalDecision:
    action: str
    approved_indices: list[int]
    timed_out: bool = False
    raw_input: str | None = None


class CLIApprovalFlow:
    def __init__(
        self,
        timeout_seconds: int = 120,
        timeout_action: str = "approve_all",
        input_func: Callable[[str], str] = input,
    ):
        self._timeout_seconds = timeout_seconds
        self._timeout_action = timeout_action
        self._input_func = input_func

    async def request(self, picks: DailyPicks) -> ApprovalDecision:
        if not picks.picks:
            return ApprovalDecision(action="approve_all", approved_indices=[])

        print("\nMain trader recommendations:")
        for idx, pick in enumerate(picks.picks, start=1):
            print(f"  {idx}. {pick.ticker} ({pick.allocation_pct:.1f}%) - {pick.reasoning[:100]}")
        print("\nApprove options:")
        print("  [A]pprove all")
        print("  [R]eject all")
        print("  [1,2,...] approve specific pick numbers")

        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(self._input_func, "Your decision: "),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            return self._timeout_decision(len(picks.picks))

        return self._parse(raw, len(picks.picks))

    def _timeout_decision(self, pick_count: int) -> ApprovalDecision:
        if self._timeout_action == "reject_all":
            return ApprovalDecision(
                action="reject_all",
                approved_indices=[],
                timed_out=True,
                raw_input=None,
            )
        return ApprovalDecision(
            action="approve_all",
            approved_indices=list(range(pick_count)),
            timed_out=True,
            raw_input=None,
        )

    def _parse(self, raw: str, pick_count: int) -> ApprovalDecision:
        value = raw.strip().lower()
        if value in {"", "a", "approve", "approve_all"}:
            return ApprovalDecision(
                action="approve_all",
                approved_indices=list(range(pick_count)),
                raw_input=raw,
            )
        if value in {"r", "reject", "reject_all"}:
            return ApprovalDecision(action="reject_all", approved_indices=[], raw_input=raw)

        approved_indices: list[int] = []
        for part in value.split(","):
            part = part.strip()
            if not part:
                continue
            if not part.isdigit():
                continue
            choice = int(part)
            if 1 <= choice <= pick_count:
                approved_indices.append(choice - 1)

        approved_indices = sorted(set(approved_indices))
        if not approved_indices:
            return ApprovalDecision(action="reject_all", approved_indices=[], raw_input=raw)
        return ApprovalDecision(
            action="approve_subset",
            approved_indices=approved_indices,
            raw_input=raw,
        )

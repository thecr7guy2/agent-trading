"""Print the next scheduled fire times so you can verify the cron config."""

import asyncio
from datetime import datetime

from src.orchestrator.scheduler import OrchestratorScheduler


async def main() -> None:
    sched = OrchestratorScheduler()
    sched.configure_jobs()

    print(f"\nTrade days config: {sched._settings.scheduler_trade_days}")
    print(f"Execute time:      {sched._settings.scheduler_execute_time}")
    print(f"EOD time:          {sched._settings.scheduler_eod_time}")
    print()

    for job in sched.scheduler.get_jobs():
        try:
            next_run = job.next_run_time
        except Exception:
            next_run = None
        if next_run is None:
            try:
                next_run = job.trigger.get_next_fire_time(
                    None,
                    datetime.now(sched.scheduler.timezone),
                )
            except Exception:
                next_run = "pending"
        print(f"  {job.id:<30} next run → {next_run}")

    print()


asyncio.run(main())

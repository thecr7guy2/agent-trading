from types import SimpleNamespace

from src.orchestrator.scheduler import OrchestratorScheduler


class _DummySupervisor:
    async def collect_reddit_round(self):
        return {"status": "ok"}

    async def run_decision_cycle(self, require_approval: bool = True):
        return {"status": "ok", "require_approval": require_approval}

    async def run_end_of_day(self):
        return {"status": "ok"}


class TestOrchestratorScheduler:
    def test_configure_jobs(self):
        settings = SimpleNamespace(
            orchestrator_timezone="Europe/Berlin",
            scheduler_collect_times="08:00,12:00,16:30",
            scheduler_execute_time="17:10",
            scheduler_eod_time="17:35",
        )
        scheduler = OrchestratorScheduler(supervisor=_DummySupervisor(), settings=settings)
        scheduler.configure_jobs()

        jobs = scheduler.scheduler.get_jobs()
        job_ids = sorted(job.id for job in jobs)

        assert job_ids == [
            "collect_round_1",
            "collect_round_2",
            "collect_round_3",
            "decision_and_execution",
            "end_of_day_snapshot",
        ]

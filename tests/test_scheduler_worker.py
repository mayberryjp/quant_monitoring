from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from unittest.mock import patch

from cron_runner.scheduling.models import JobDefinition
from cron_runner.workers.scheduler_worker import MAX_THREAD_POOL_SIZE, SchedulerWorker


def make_job(name: str, allow_concurrent: bool = False) -> JobDefinition:
    return JobDefinition(
        name=name,
        script="scripts/x.py",
        schedule="* * * * *",
        timeout_seconds=30,
        allow_concurrent=allow_concurrent,
    )


def test_executor_pool_size_is_fixed_regardless_of_initial_job_count(tmp_path):
    # Regression test: the pool must not be sized to the job count at startup, since
    # jobs can be added later via a schedule reload without recreating the executor.
    path = tmp_path / "schedule.yaml"
    path.write_text(
        "jobs:\n  - name: only_job\n    script: x.py\n    schedule: '* * * * *'\n    timeout_seconds: 5\n"
    )
    with patch("cron_runner.workers.scheduler_worker.settings") as mocked_settings:
        mocked_settings.schedule_file = str(path)
        worker = SchedulerWorker()
        worker.load_initial_schedule()

    assert worker.executor is not None
    assert worker.executor._max_workers == MAX_THREAD_POOL_SIZE


def test_dispatch_skips_when_already_running_and_not_allow_concurrent():
    worker = SchedulerWorker()
    worker.executor = ThreadPoolExecutor(1)
    job = make_job("job_a", allow_concurrent=False)
    worker.registry.mark_started(job.name, "exec-1", datetime.now(UTC))

    with patch.object(worker.executor, "submit") as submit:
        worker._dispatch(job, "2026-08-20T02:00")
        submit.assert_not_called()


def test_dispatch_allows_when_allow_concurrent_true():
    worker = SchedulerWorker()
    worker.executor = ThreadPoolExecutor(1)
    job = make_job("job_a", allow_concurrent=True)
    worker.registry.mark_started(job.name, "exec-1", datetime.now(UTC))

    with patch.object(worker.executor, "submit") as submit:
        worker._dispatch(job, "2026-08-20T02:00")
        submit.assert_called_once()


def test_dispatch_runs_when_not_already_running():
    worker = SchedulerWorker()
    worker.executor = ThreadPoolExecutor(1)
    job = make_job("job_a")

    with patch.object(worker.executor, "submit") as submit:
        worker._dispatch(job, "2026-08-20T02:00")
        submit.assert_called_once()
        assert (job.name, "2026-08-20T02:00") in worker._dispatched_this_minute


def test_reload_keeps_previous_schedule_on_invalid_file(tmp_path):
    valid_path = tmp_path / "schedule.yaml"
    valid_path.write_text(
        "jobs:\n  - name: job_a\n    script: x.py\n    schedule: '* * * * *'\n    timeout_seconds: 5\n"
    )

    with patch("cron_runner.workers.scheduler_worker.settings") as mocked_settings:
        mocked_settings.schedule_file = str(valid_path)
        worker = SchedulerWorker()
        worker.load_initial_schedule()
        original_schedule = worker.schedule

        valid_path.write_text("jobs:\n  - name: broken\n    schedule: 'not-a-cron'\n")
        worker.request_reload()
        worker._maybe_reload()

        assert worker.schedule is original_schedule

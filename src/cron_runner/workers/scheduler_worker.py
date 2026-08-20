"""Main scheduler worker: a persistent process that polls the schedule file every
`poll_interval_seconds` and dispatches due jobs. This loop is the container's primary,
long-running process (run under supervisord with autorestart) and must never exit on
its own except via SIGTERM/SIGINT -- per-job and per-iteration errors are caught and
logged so a single bad job or transient error can never kill the loop.
"""
from __future__ import annotations

import signal
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

from cron_runner.config import settings
from cron_runner.heartbeat import write_heartbeat
from cron_runner.logging import configure_logging, get_logger
from cron_runner.repository import run_history
from cron_runner.scheduling.loader import ScheduleLoadError, load_schedule_file
from cron_runner.scheduling.matcher import due_jobs
from cron_runner.scheduling.models import JobDefinition, ScheduleFile
from cron_runner.workers.job_registry import JobRegistry
from cron_runner.workers.job_runner import STATUS_SUCCESS, run_job

log = get_logger("cron_runner.scheduler_worker")

REPO_ROOT = Path(__file__).resolve().parents[3]
MAX_THREAD_POOL_SIZE = 32


class SchedulerState:
    """Process-wide state read by the health API to answer /ready."""

    def __init__(self) -> None:
        self.heartbeat_at: datetime | None = None
        self.schedule_loaded: bool = False
        self.job_count: int = 0
        self.last_reload_error: str | None = None

    def heartbeat_age_seconds(self) -> float | None:
        if self.heartbeat_at is None:
            return None
        return (datetime.now(UTC) - self.heartbeat_at).total_seconds()


# module-level singleton so the API process (or in-process health checks) can read it
state = SchedulerState()


class SchedulerWorker:
    """Owns the persistent poll loop: load schedule -> loop forever -> dispatch due jobs."""

    def __init__(self) -> None:
        self.registry = JobRegistry()
        self.schedule: ScheduleFile | None = None
        self._dispatched_this_minute: set[tuple[str, str]] = set()
        self._stop_requested = False
        self._reload_requested = False
        self.executor: ThreadPoolExecutor | None = None

    def load_initial_schedule(self) -> None:
        """Fatal on failure: an invalid schedule at startup must stop the process (fail fast)."""
        self.schedule = load_schedule_file(settings.schedule_file)
        state.schedule_loaded = True
        state.job_count = len(self.schedule.jobs)
        # Fixed at MAX_THREAD_POOL_SIZE (not sized to the current job count) so that jobs
        # added later via a schedule reload always have a free worker thread available --
        # ThreadPoolExecutor cannot be resized after creation, so sizing it to the initial
        # job count would silently serialize jobs added after startup.
        self.executor = ThreadPoolExecutor(max_workers=MAX_THREAD_POOL_SIZE, thread_name_prefix="job")
        log.info("loaded %s job(s) from %s", state.job_count, settings.schedule_file)

    def request_reload(self, *_args: object) -> None:
        self._reload_requested = True

    def request_stop(self, _signum: int, _frame: FrameType | None = None) -> None:
        log.info("shutdown signal received, stopping scheduler loop")
        self._stop_requested = True

    def _maybe_reload(self) -> None:
        if not self._reload_requested:
            return
        self._reload_requested = False
        try:
            new_schedule = load_schedule_file(settings.schedule_file)
        except ScheduleLoadError as exc:
            # fail-safe, not fail-open: keep running the previous valid schedule
            state.last_reload_error = str(exc)
            log.error("schedule reload failed, keeping previous schedule active: %s", exc)
            return
        self.schedule = new_schedule
        state.job_count = len(new_schedule.jobs)
        state.last_reload_error = None
        log.info("reloaded schedule: %s job(s) now active", state.job_count)

    def _dispatch(self, job: JobDefinition, minute_key: str) -> None:
        if self.registry.is_running(job.name) and not job.allow_concurrent:
            log.warning(
                "job=%s status=skipped_overlap reason=previous_instance_still_running",
                job.name,
            )
            return
        assert self.executor is not None
        self._dispatched_this_minute.add((job.name, minute_key))
        self.executor.submit(self._execute_with_retries, job)

    def _execute_with_retries(self, job: JobDefinition) -> None:
        attempt = 1
        max_attempts = 1 + max(job.max_retries, 0)
        while attempt <= max_attempts:
            execution_id = str(uuid.uuid4())
            started_at = datetime.now(UTC)
            self.registry.mark_started(job.name, execution_id, started_at)
            run_history.record_start(execution_id, job.name, attempt, started_at)
            try:
                result = run_job(job, REPO_ROOT, attempt=attempt, execution_id=execution_id)
                run_history.record_completion(result)
            finally:
                self.registry.mark_finished(job.name)

            if result.status == STATUS_SUCCESS or attempt >= max_attempts:
                break
            attempt += 1
            if job.retry_delay_seconds:
                time.sleep(job.retry_delay_seconds)

    def run_forever(self) -> None:
        """The persistent main loop. Blocks until SIGTERM/SIGINT."""
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, self.request_reload)

        self.load_initial_schedule()
        log.info("scheduler worker started, polling every %ss", settings.poll_interval_seconds)

        last_reload_check = time.monotonic()
        last_seen_minute: str | None = None

        while not self._stop_requested:
            try:
                if settings.config_reload_seconds and (
                    time.monotonic() - last_reload_check >= settings.config_reload_seconds
                ):
                    self.request_reload()
                    last_reload_check = time.monotonic()

                self._maybe_reload()

                now = datetime.now(UTC)
                minute_key = now.strftime("%Y-%m-%dT%H:%M")

                if minute_key != last_seen_minute and self.schedule is not None:
                    for job in due_jobs(self.schedule, now):
                        if (job.name, minute_key) not in self._dispatched_this_minute:
                            self._dispatch(job, minute_key)
                    last_seen_minute = minute_key
                    # drop dedupe entries from prior minutes to keep the set bounded
                    self._dispatched_this_minute = {
                        entry for entry in self._dispatched_this_minute if entry[1] == minute_key
                    }

                state.heartbeat_at = now
                write_heartbeat(state.schedule_loaded, state.job_count, state.last_reload_error)
            except Exception:
                log.exception("scheduler loop iteration failed, continuing")

            time.sleep(settings.poll_interval_seconds)

        if self.executor is not None:
            self.executor.shutdown(wait=True)
        log.info("scheduler worker stopped")


def main() -> None:
    configure_logging(settings.log_level)
    worker = SchedulerWorker()
    worker.run_forever()


if __name__ == "__main__":
    main()

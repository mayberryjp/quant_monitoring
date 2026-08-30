"""Cron-expression matching helpers built on top of croniter."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter

from cron_runner.scheduling.models import JobDefinition, ScheduleFile

# Weekday-only jobs (run_weekend=False) are evaluated against New York wall-clock
# time, which is the container's configured timezone.
WEEKEND_TIMEZONE = ZoneInfo("America/New_York")


def is_due(job: JobDefinition, at: datetime, tz_name: str = "UTC") -> bool:
    """Return True if `job`'s cron expression matches the minute containing `at`.

    `at` is converted to `tz_name` first so a job's schedule (e.g. "0 16 * * *")
    is evaluated against that timezone's wall-clock time, not UTC.

    Jobs without `run_weekend` never fire on Saturday or Sunday in New York time.
    """
    local_at = at.astimezone(ZoneInfo(tz_name))
    truncated = local_at.replace(second=0, microsecond=0)
    if not croniter.match(job.schedule, truncated):
        return False
    if not job.run_weekend and at.astimezone(WEEKEND_TIMEZONE).weekday() >= 5:
        return False
    return True


def due_jobs(schedule: ScheduleFile, at: datetime) -> list[JobDefinition]:
    """Return all enabled jobs whose schedule matches the minute containing `at`.

    Each job is evaluated in its own `timezone` if set, falling back to the
    schedule file's default `timezone` (UTC unless overridden).
    """
    return [
        job
        for job in schedule.jobs
        if job.enabled and is_due(job, at, job.timezone or schedule.timezone)
    ]

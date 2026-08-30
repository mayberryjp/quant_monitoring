from datetime import UTC, datetime

from cron_runner.scheduling.matcher import due_jobs, is_due
from cron_runner.scheduling.models import JobDefinition, ScheduleFile


def make_job(name: str, schedule: str, enabled: bool = True) -> JobDefinition:
    return JobDefinition(
        name=name, script="scripts/x.py", schedule=schedule, timeout_seconds=30, enabled=enabled
    )


def test_every_minute_schedule_always_matches():
    job = make_job("every_minute", "* * * * *")
    at = datetime(2026, 8, 20, 2, 17, 42, tzinfo=UTC)
    assert is_due(job, at) is True


def test_every_five_minutes_matches_only_on_multiples():
    job = make_job("every_five", "*/5 * * * *")
    assert is_due(job, datetime(2026, 8, 20, 2, 15, 0, tzinfo=UTC)) is True
    assert is_due(job, datetime(2026, 8, 20, 2, 16, 0, tzinfo=UTC)) is False


def test_due_jobs_excludes_disabled_jobs():
    schedule = ScheduleFile(
        jobs=[
            make_job("enabled_job", "* * * * *", enabled=True),
            make_job("disabled_job", "* * * * *", enabled=False),
        ]
    )
    at = datetime(2026, 8, 20, 2, 0, 0, tzinfo=UTC)
    names = {job.name for job in due_jobs(schedule, at)}
    assert names == {"enabled_job"}


def test_due_jobs_excludes_non_matching_schedule():
    schedule = ScheduleFile(jobs=[make_job("hourly", "0 * * * *")])
    at = datetime(2026, 8, 20, 2, 30, 0, tzinfo=UTC)
    assert due_jobs(schedule, at) == []


def test_job_timezone_overrides_schedule_default():
    # 07:00 UTC == 16:00 Asia/Tokyo (UTC+9, no DST)
    job = JobDefinition(
        name="tokyo_job",
        script="scripts/x.py",
        schedule="0 16 * * *",
        timeout_seconds=30,
        timezone="Asia/Tokyo",
    )
    at_utc = datetime(2026, 8, 20, 7, 0, 0, tzinfo=UTC)
    assert is_due(job, at_utc, job.timezone) is True
    assert is_due(job, datetime(2026, 8, 20, 6, 0, 0, tzinfo=UTC), job.timezone) is False


def test_due_jobs_uses_schedule_default_timezone_when_job_has_none():
    schedule = ScheduleFile(
        timezone="Asia/Tokyo",
        jobs=[make_job("tokyo_default", "0 16 * * *")],
    )
    at_utc = datetime(2026, 8, 20, 7, 0, 0, tzinfo=UTC)
    assert due_jobs(schedule, at_utc) != []


def test_weekday_only_job_does_not_run_on_weekend():
    # 2026-08-22 is a Saturday in New York (16:00 UTC == 12:00 EDT).
    job = make_job("weekday_job", "0 12 * * *")
    saturday = datetime(2026, 8, 22, 16, 0, 0, tzinfo=UTC)
    friday = datetime(2026, 8, 21, 16, 0, 0, tzinfo=UTC)
    assert is_due(job, saturday, "America/New_York") is False
    assert is_due(job, friday, "America/New_York") is True


def test_run_weekend_job_runs_on_weekend():
    job = JobDefinition(
        name="weekend_job",
        script="scripts/x.py",
        schedule="0 12 * * *",
        timeout_seconds=30,
        run_weekend=True,
    )
    saturday = datetime(2026, 8, 22, 16, 0, 0, tzinfo=UTC)
    assert is_due(job, saturday, "America/New_York") is True

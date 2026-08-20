"""GET /jobs -- lists jobs currently defined in schedule.yaml.

Reads the schedule file directly rather than the scheduler's in-memory state,
since the API and scheduler run as separate OS processes under supervisord
and cannot share in-memory objects (see docs/specs/run_history_api_spec.md).
"""
from __future__ import annotations

from bottle import Bottle, response

from cron_runner.config import settings
from cron_runner.logging import get_logger
from cron_runner.scheduling.loader import ScheduleLoadError, load_schedule_file

log = get_logger("cron_runner.api.jobs")


def register_job_routes(app: Bottle) -> None:
    @app.get("/jobs")
    def list_jobs_route() -> dict:
        try:
            schedule = load_schedule_file(settings.schedule_file)
        except ScheduleLoadError as exc:
            log.exception("failed to load schedule file for /jobs")
            response.status = 500
            return {"status": "error", "code": "internal_error", "error": str(exc)}

        jobs = [
            {
                "name": job.name,
                "schedule": job.schedule,
                "timezone": job.timezone or schedule.timezone,
                "enabled": job.enabled,
                "allow_concurrent": job.allow_concurrent,
                "timeout_seconds": job.timeout_seconds,
                "max_retries": job.max_retries,
            }
            for job in schedule.jobs
        ]
        return {"status": "ok", "count": len(jobs), "jobs": jobs}

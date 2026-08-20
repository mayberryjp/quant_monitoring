"""Required operational endpoints: GET /health and GET /ready.

This service has no external business API -- the API process exists solely to give
the container a standard health/readiness surface (per BACKEND_CODING_STANDARDS.md
section 6). The API and scheduler run as separate OS processes under supervisord, so
readiness is derived from the scheduler's file-based heartbeat (see cron_runner.heartbeat)
rather than in-process state.
"""
from __future__ import annotations

from bottle import Bottle, response

from cron_runner.config import settings
from cron_runner.db import check_database
from cron_runner.heartbeat import heartbeat_age_seconds, read_heartbeat

SERVICE_NAME = "cron-runner-api"
HEARTBEAT_STALE_MULTIPLIER = 3


def register_health_routes(app: Bottle) -> None:
    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": SERVICE_NAME}

    @app.get("/ready")
    def ready() -> dict:
        errors: list[str] = []

        payload = read_heartbeat()
        if not payload or not payload.get("schedule_loaded"):
            errors.append("schedule file has not loaded successfully")

        age = heartbeat_age_seconds(payload)
        stale_after = settings.poll_interval_seconds * HEARTBEAT_STALE_MULTIPLIER
        if age is None or age > stale_after:
            errors.append("scheduler heartbeat is stale or missing")

        db_ok, db_detail = check_database()
        if not db_ok:
            errors.append(db_detail)

        if errors:
            response.status = 503
            return {
                "status": "error",
                "code": "not_ready",
                "error": "; ".join(errors),
            }

        return {
            "status": "ok",
            "job_count": (payload or {}).get("job_count", 0),
            "heartbeat_age_seconds": age,
        }

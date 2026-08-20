"""Read-only run-history endpoints for frontend consumption.

Per docs/specs/run_history_api_spec.md: GET /runs, GET /runs/<execution_id>,
GET /jobs/<job_name>/runs/latest. Backed by the cron_runner_job_runs table.
"""
from __future__ import annotations

from datetime import datetime

from bottle import Bottle, request, response

from cron_runner.logging import get_logger
from cron_runner.repository import run_history

log = get_logger("cron_runner.api.runs")

VALID_STATUSES = frozenset(("started", "success", "failed", "timeout"))
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class _ValidationError(Exception):
    pass


def _int_param(raw: str | None, *, default: int, ge: int, le: int | None = None) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise _ValidationError(f"expected an integer, got {raw!r}") from exc
    if value < ge or (le is not None and value > le):
        bound = f">= {ge}" + (f" and <= {le}" if le is not None else "")
        raise _ValidationError(f"value must be {bound}")
    return value


def _status_param(raw: str | None) -> str | None:
    if raw is None or raw == "":
        return None
    if raw not in VALID_STATUSES:
        raise _ValidationError(f"invalid status {raw!r}, expected one of {sorted(VALID_STATUSES)}")
    return raw


def _datetime_param(raw: str | None, name: str) -> datetime | None:
    if raw is None or raw == "":
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise _ValidationError(f"invalid {name}: {raw!r} is not ISO 8601") from exc


def _validation_error_response(message: str) -> dict:
    response.status = 422
    return {"status": "error", "code": "validation_error", "error": message}


def _internal_error_response(exc: Exception) -> dict:
    log.exception("run history query failed")
    response.status = 500
    return {"status": "error", "code": "internal_error", "error": type(exc).__name__}


def register_run_routes(app: Bottle) -> None:
    @app.get("/runs")
    def list_runs_route() -> dict:
        try:
            job_name = request.query.get("job_name") or None
            status = _status_param(request.query.get("status"))
            started_from = _datetime_param(request.query.get("from"), "from")
            started_to = _datetime_param(request.query.get("to"), "to")
            limit = _int_param(request.query.get("limit"), default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
            offset = _int_param(request.query.get("offset"), default=0, ge=0)
        except _ValidationError as exc:
            return _validation_error_response(str(exc))

        try:
            results = run_history.list_runs(
                job_name=job_name,
                status=status,
                started_from=started_from,
                started_to=started_to,
                limit=limit,
                offset=offset,
            )
        except Exception as exc:  # noqa: BLE001
            return _internal_error_response(exc)

        return {"status": "ok", "count": len(results), "limit": limit, "offset": offset, "results": results}

    @app.get("/runs/<execution_id>")
    def run_detail_route(execution_id: str) -> dict:
        try:
            run = run_history.get_run(execution_id)
        except Exception as exc:  # noqa: BLE001
            return _internal_error_response(exc)

        if run is None:
            response.status = 404
            return {"status": "error", "code": "not_found", "error": "run not found"}
        return {"status": "ok", "run": run}

    @app.get("/jobs/<job_name>/runs/latest")
    def latest_run_route(job_name: str) -> dict:
        try:
            run = run_history.get_latest_run(job_name)
        except Exception as exc:  # noqa: BLE001
            return _internal_error_response(exc)

        if run is None:
            response.status = 404
            return {
                "status": "error",
                "code": "not_found",
                "error": f"no runs found for job {job_name!r}",
            }
        return {"status": "ok", "run": run}

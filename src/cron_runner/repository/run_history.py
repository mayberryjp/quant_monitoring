"""Insert/update/query job_runs rows. Writes never raise: DB failures degrade to
log-only history. Reads (used by the run-history API) do raise on failure so the
API can surface a 500/503, per docs/specs/run_history_api_spec.md."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import insert, select, update

from cron_runner.db import get_engine
from cron_runner.logging import get_logger
from cron_runner.repository.models import job_runs

if TYPE_CHECKING:
    from cron_runner.workers.job_runner import JobRunResult

log = get_logger("cron_runner.run_history")

LIST_COLUMNS = (
    job_runs.c.id,
    job_runs.c.job_name,
    job_runs.c.attempt,
    job_runs.c.status,
    job_runs.c.exit_code,
    job_runs.c.started_at,
    job_runs.c.finished_at,
    job_runs.c.duration_ms,
    job_runs.c.error_message,
)


def record_start(execution_id: str, job_name: str, attempt: int, started_at: datetime) -> None:
    try:
        with get_engine().begin() as conn:
            conn.execute(
                insert(job_runs).values(
                    id=execution_id,
                    job_name=job_name,
                    attempt=attempt,
                    status="started",
                    started_at=started_at,
                )
            )
    except Exception:
        log.exception(
            "job=%s execution_id=%s failed to record run start in database",
            job_name, execution_id,
        )


def record_completion(result: JobRunResult) -> None:
    try:
        with get_engine().begin() as conn:
            conn.execute(
                update(job_runs)
                .where(job_runs.c.id == result.execution_id)
                .values(
                    status=result.status,
                    exit_code=result.exit_code,
                    finished_at=result.finished_at,
                    duration_ms=result.duration_ms,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    error_message=result.error_message,
                )
            )
    except Exception:
        log.exception(
            "job=%s execution_id=%s failed to record run completion in database",
            result.job_name, result.execution_id,
        )


def _row_to_dict(row: Any, *, include_output: bool) -> dict[str, Any]:
    data = {
        "execution_id": row.id,
        "job_name": row.job_name,
        "attempt": row.attempt,
        "status": row.status,
        "exit_code": row.exit_code,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "duration_ms": row.duration_ms,
        "error_message": row.error_message,
    }
    if include_output:
        data["stdout"] = row.stdout
        data["stderr"] = row.stderr
    return data


def list_runs(
    *,
    job_name: str | None = None,
    status: str | None = None,
    started_from: datetime | None = None,
    started_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    query = select(*LIST_COLUMNS).order_by(job_runs.c.started_at.desc())
    if job_name is not None:
        query = query.where(job_runs.c.job_name == job_name)
    if status is not None:
        query = query.where(job_runs.c.status == status)
    if started_from is not None:
        query = query.where(job_runs.c.started_at >= started_from)
    if started_to is not None:
        query = query.where(job_runs.c.started_at <= started_to)
    query = query.limit(limit).offset(offset)

    with get_engine().connect() as conn:
        rows = conn.execute(query).all()
    return [_row_to_dict(row, include_output=False) for row in rows]


def get_run(execution_id: str) -> dict[str, Any] | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(job_runs).where(job_runs.c.id == execution_id)).first()
    return _row_to_dict(row, include_output=True) if row is not None else None


def get_latest_run(job_name: str) -> dict[str, Any] | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(job_runs)
            .where(job_runs.c.job_name == job_name)
            .order_by(job_runs.c.started_at.desc())
            .limit(1)
        ).first()
    return _row_to_dict(row, include_output=True) if row is not None else None

"""Insert/update job_runs rows. Never raises: DB failures degrade to log-only history."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import insert, update

from cron_runner.db import get_engine
from cron_runner.logging import get_logger
from cron_runner.repository.models import job_runs

if TYPE_CHECKING:
    from cron_runner.workers.job_runner import JobRunResult

log = get_logger("cron_runner.run_history")


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

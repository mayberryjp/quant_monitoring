"""Subprocess execution of a single job with timeout handling and output capture."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from cron_runner.config import settings
from cron_runner.logging import get_logger
from cron_runner.scheduling.models import JobDefinition

log = get_logger("cron_runner.job_runner")

STATUS_STARTED = "started"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"


@dataclass
class JobRunResult:
    execution_id: str
    job_name: str
    attempt: int
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error_message: str | None = None
    extra_fields: dict = field(default_factory=dict)


def _truncate(text: str) -> str:
    limit = settings.max_output_bytes
    if not limit or len(text.encode("utf-8")) <= limit:
        return text
    encoded = text.encode("utf-8")[:limit]
    return encoded.decode("utf-8", errors="ignore") + "...truncated"


def run_job(
    job: JobDefinition, repo_root: Path, attempt: int = 1, execution_id: str | None = None
) -> JobRunResult:
    """Run `job` as an isolated subprocess and return its outcome. Never raises."""
    execution_id = execution_id or str(uuid.uuid4())
    started_at = datetime.now(UTC)
    start_perf = time.monotonic()

    log.info(
        "job=%s execution_id=%s status=%s attempt=%s",
        job.name, execution_id, STATUS_STARTED, attempt,
    )

    command = [sys.executable, job.script, *job.args]
    child_env = {**os.environ, **job.env}

    try:
        completed = subprocess.run(
            command,
            cwd=str(repo_root),
            env=child_env,
            timeout=job.timeout_seconds,
            capture_output=True,
            text=True,
            check=False,
        )
        duration_ms = int((time.monotonic() - start_perf) * 1000)
        finished_at = datetime.now(UTC)
        stdout = _truncate(completed.stdout or "")
        stderr = _truncate(completed.stderr or "")
        status = STATUS_SUCCESS if completed.returncode == 0 else STATUS_FAILED

        _log_output(job.name, execution_id, stdout, stderr)

        level = log.info if status == STATUS_SUCCESS else log.error
        level(
            "job=%s execution_id=%s status=%s exit_code=%s duration_ms=%s attempt=%s",
            job.name, execution_id, status, completed.returncode, duration_ms, attempt,
        )

        return JobRunResult(
            execution_id=execution_id,
            job_name=job.name,
            attempt=attempt,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - start_perf) * 1000)
        finished_at = datetime.now(UTC)
        stdout = _truncate((exc.stdout or "") if isinstance(exc.stdout, str) else "")
        stderr = _truncate((exc.stderr or "") if isinstance(exc.stderr, str) else "")

        _log_output(job.name, execution_id, stdout, stderr)
        log.error(
            "job=%s execution_id=%s status=%s duration_ms=%s attempt=%s",
            job.name, execution_id, STATUS_TIMEOUT, duration_ms, attempt,
        )

        return JobRunResult(
            execution_id=execution_id,
            job_name=job.name,
            attempt=attempt,
            status=STATUS_TIMEOUT,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            exit_code=None,
            stdout=stdout,
            stderr=stderr,
            error_message=f"job exceeded timeout_seconds={job.timeout_seconds}",
        )
    except Exception as exc:
        duration_ms = int((time.monotonic() - start_perf) * 1000)
        finished_at = datetime.now(UTC)
        log.exception(
            "job=%s execution_id=%s status=%s attempt=%s error launching subprocess",
            job.name, execution_id, STATUS_FAILED, attempt,
        )
        return JobRunResult(
            execution_id=execution_id,
            job_name=job.name,
            attempt=attempt,
            status=STATUS_FAILED,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            exit_code=None,
            error_message=str(exc),
        )


def _log_output(job_name: str, execution_id: str, stdout: str, stderr: str) -> None:
    if stdout:
        for line in stdout.splitlines():
            log.info("job=%s execution_id=%s stdout: %s", job_name, execution_id, line)
    if stderr:
        for line in stderr.splitlines():
            log.warning("job=%s execution_id=%s stderr: %s", job_name, execution_id, line)

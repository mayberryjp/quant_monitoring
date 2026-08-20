import textwrap
from pathlib import Path

from cron_runner.scheduling.models import JobDefinition
from cron_runner.workers.job_runner import (
    STATUS_FAILED,
    STATUS_SUCCESS,
    STATUS_TIMEOUT,
    run_job,
)


def write_script(repo_root: Path, name: str, body: str) -> str:
    script_path = repo_root / name
    script_path.write_text(textwrap.dedent(body))
    return name


def test_successful_job_reports_success(repo_root: Path):
    script = write_script(
        repo_root,
        "success.py",
        """
        print("hello stdout")
        """,
    )
    job = JobDefinition(name="job", script=script, schedule="* * * * *", timeout_seconds=10)

    result = run_job(job, repo_root)

    assert result.status == STATUS_SUCCESS
    assert result.exit_code == 0
    assert "hello stdout" in result.stdout
    assert result.duration_ms is not None


def test_non_zero_exit_reports_failed(repo_root: Path):
    script = write_script(
        repo_root,
        "failing.py",
        """
        import sys
        print("boom", file=sys.stderr)
        sys.exit(1)
        """,
    )
    job = JobDefinition(name="job", script=script, schedule="* * * * *", timeout_seconds=10)

    result = run_job(job, repo_root)

    assert result.status == STATUS_FAILED
    assert result.exit_code == 1
    assert "boom" in result.stderr


def test_timeout_kills_process(repo_root: Path):
    script = write_script(
        repo_root,
        "slow.py",
        """
        import time
        time.sleep(5)
        """,
    )
    job = JobDefinition(name="job", script=script, schedule="* * * * *", timeout_seconds=1)

    result = run_job(job, repo_root)

    assert result.status == STATUS_TIMEOUT
    assert result.exit_code is None
    assert result.error_message is not None


def test_attempt_number_is_recorded(repo_root: Path):
    script = write_script(repo_root, "ok.py", "print('ok')")
    job = JobDefinition(name="job", script=script, schedule="* * * * *", timeout_seconds=10)

    result = run_job(job, repo_root, attempt=3)

    assert result.attempt == 3


def test_execution_id_can_be_supplied(repo_root: Path):
    script = write_script(repo_root, "ok.py", "print('ok')")
    job = JobDefinition(name="job", script=script, schedule="* * * * *", timeout_seconds=10)

    result = run_job(job, repo_root, execution_id="fixed-id")

    assert result.execution_id == "fixed-id"

from datetime import UTC, datetime

from sqlalchemy import create_engine, select

from cron_runner.repository import run_history
from cron_runner.repository.models import job_runs, metadata
from cron_runner.workers.job_runner import JobRunResult


def make_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    metadata.create_all(engine)
    return engine


def test_record_start_then_completion_updates_row(tmp_path, monkeypatch):
    engine = make_engine(tmp_path)
    monkeypatch.setattr(run_history, "get_engine", lambda: engine)

    started_at = datetime.now(UTC)
    run_history.record_start("exec-1", "job_a", 1, started_at)

    with engine.connect() as conn:
        row = conn.execute(select(job_runs).where(job_runs.c.id == "exec-1")).first()
    assert row is not None
    assert row.status == "started"

    result = JobRunResult(
        execution_id="exec-1",
        job_name="job_a",
        attempt=1,
        status="success",
        started_at=started_at,
        finished_at=datetime.now(UTC),
        duration_ms=42,
        exit_code=0,
        stdout="hello",
        stderr="",
    )
    run_history.record_completion(result)

    with engine.connect() as conn:
        row = conn.execute(select(job_runs).where(job_runs.c.id == "exec-1")).first()
    assert row.status == "success"
    assert row.exit_code == 0
    assert row.stdout == "hello"


def test_record_start_failure_is_swallowed(monkeypatch):
    def broken_engine():
        raise RuntimeError("db down")

    monkeypatch.setattr(run_history, "get_engine", broken_engine)

    # must not raise
    run_history.record_start("exec-2", "job_a", 1, datetime.now(UTC))

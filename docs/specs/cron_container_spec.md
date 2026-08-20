# Spec: Scheduled Script Runner ("Cron Container")

## 1. Purpose

A containerized service that runs a set of independent Python scripts on a schedule, defined
declaratively in a single cron-style definition file. A long-running scheduler worker process
polls the definition file's schedules, launches due scripts as isolated subprocesses, prevents
duplicate/overlapping runs of the same job, writes every run's outcome to a database, and emits
robust stdout logs for every execution. This repo follows
[BACKEND_CODING_STANDARDS.md](https://github.com/mayberryjp/coding_standards/blob/main/BACKEND_CODING_STANDARDS.md),
adapted where noted in [Section 14](#14-deviations-from-the-standard).

## 2. Repository Layout

```text
monitoring/
  .github/
    workflows/
      ci.yml
      docker-publish.yml
  alembic/
    env.py
    script.py.mako
    versions/
  src/
    cron_runner/
      api/
        app.py
        routes/
          health.py
      config.py
      db.py
      logging.py
      repository/
        run_history.py       # insert/query job_runs rows
      scheduling/
        models.py          # Pydantic models for schedule.yaml
        loader.py           # load + validate schedule file
        matcher.py           # cron-expression matching (croniter wrapper)
      workers/
        scheduler_worker.py  # main worker: polls + dispatches jobs
        job_runner.py        # subprocess execution, timeout, capture output
        job_registry.py      # tracks in-flight jobs, prevents overlap
      __init__.py
      __main__.py
  scripts/
    example_job.py
  config/
    schedule.yaml
    schedule.schema.json (generated, optional)
  tests/
    conftest.py
    test_health.py
    test_ready.py
    test_loader.py
    test_matcher.py
    test_job_runner.py
    test_scheduler_worker.py
    test_run_history.py
  .dockerignore
  .env.example
  .gitignore
  alembic.ini
  docker-compose.yml
  Dockerfile
  pyproject.toml
  README.md
  supervisord.conf
```

Rules:
- Application code lives under `src/cron_runner/`.
- User-provided job scripts live under `scripts/`, mounted or copied into the image; they are
  plain Python scripts with a `main()` entrypoint (or `if __name__ == "__main__":` block) and are
  executed as subprocesses — never imported into the scheduler's process space.
- The schedule definition lives at `config/schedule.yaml`, overridable via the
  `CRON_SCHEDULE_FILE` environment variable.

## 3. Schedule Definition File

`config/schedule.yaml` is the single source of truth for what runs and when. Standard 5-field
cron syntax (`minute hour day month weekday`) is used, evaluated in UTC by default.

```yaml
timezone: "UTC"          # optional, default UTC
jobs:
  - name: "nightly_report"
    script: "scripts/nightly_report.py"
    schedule: "0 2 * * *"       # 02:00 daily
    args: ["--full"]
    timeout_seconds: 900
    enabled: true
    allow_concurrent: false     # if false, skip run if a prior instance is still running
    max_retries: 0
    env:
      REPORT_MODE: "full"

  - name: "poll_metrics"
    script: "scripts/poll_metrics.py"
    schedule: "*/5 * * * *"     # every 5 minutes
    timeout_seconds: 60
    enabled: true
    allow_concurrent: false
```

Rules:
- `name` must be unique; used as the job's log/execution identifier.
- `script` is a path relative to the repo/container root.
- `schedule` must be a valid 5-field cron expression (validated with `croniter` at load time).
- `timeout_seconds` is required (no default) so runaway scripts cannot hang the container.
- `allow_concurrent: false` (default) is enforced by the job registry (see Section 5).
- The file is loaded once at startup and re-read on a `SIGHUP` or on a configurable poll interval
  (`CRON_CONFIG_RELOAD_SECONDS`, default `0` = disabled); invalid files at reload time are
  rejected and the previous valid schedule keeps running (fail-safe, not fail-open).
- Validation errors at startup are fatal (process exits non-zero); validation errors at reload
  time are logged and the reload is discarded.

## 4. Configuration Model (Pydantic Settings)

`src/cron_runner/config.py`:

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CRON_", extra="ignore")

    schedule_file: str = Field("config/schedule.yaml", validation_alias="CRON_SCHEDULE_FILE")
    poll_interval_seconds: int = Field(60, validation_alias="CRON_POLL_INTERVAL_SECONDS")
    config_reload_seconds: int = Field(0, validation_alias="CRON_CONFIG_RELOAD_SECONDS")
    api_listen_address: str = Field("0.0.0.0", validation_alias="API_LISTEN_ADDRESS")
    api_port: int = Field(8000, validation_alias="API_PORT")
    log_level: str = "INFO"
    database_url: str = Field(..., validation_alias="DATABASE_URL")
    db_pool_size: int = 5
    db_max_overflow: int = 10

settings = Settings()
```

## 5. Main Worker Behavior

`src/cron_runner/workers/scheduler_worker.py` exposes `main()` and is the container's primary
process (run under supervisord alongside the health API).

Loop (runs every `poll_interval_seconds`, default 60s -- cron itself only has minute
resolution, so polling more often than once a minute adds overhead without adding accuracy):

1. Compute the current minute (truncated to the minute boundary) and compare against each enabled
   job's cron expression using `croniter.match(schedule, now)`.
2. For each job that is due this minute:
   - Ask the `JobRegistry` whether an instance of this job is already running.
     - If running and `allow_concurrent: false`, skip the run and log a `skipped_overlap` event
       (warning level) — this does **not** count as a failure.
     - Otherwise, dispatch the job to the `JobRunner` in a dedicated worker thread from a bounded
       `ThreadPoolExecutor` (pool size configurable, default = number of jobs, capped at 32).
   - Guarantee each job fires **at most once per matching minute** by tracking
     `(job_name, minute_bucket)` already dispatched, so a slow poll loop can never double-fire.
3. Emit a heartbeat timestamp on every loop iteration, written to a small local file
   (`CRON_HEARTBEAT_FILE`, default `/tmp/cron_runner_heartbeat.json`) alongside schedule-loaded
   state and job count. A file is used (rather than in-process memory) because the scheduler and
   the health API run as separate OS processes under supervisord (Section 10) and cannot share
   in-memory state; `/ready` reads this file to distinguish liveness from a wedged loop.
4. Catch and log all exceptions per-job and per-loop-iteration; a single job's failure or a
   scheduling error must never crash the worker process.

`JobRegistry` (`job_registry.py`) is a thread-safe in-memory map of job name -> running state
(`pid`, `started_at`, `execution_id`), used purely for overlap prevention and `/ready` reporting.
It is not the durable record of what ran — that is the `job_runs` database table (Section 8).

## 6. Job Execution

`job_runner.py` runs a job as an isolated subprocess:

```python
subprocess.run(
    [sys.executable, job.script, *job.args],
    cwd=repo_root,
    env={**os.environ, **job.env},
    timeout=job.timeout_seconds,
    capture_output=True,
    text=True,
)
```

Rules:
- Each execution gets a UUID `execution_id` used to correlate all log lines and its `job_runs`
  database row.
- `stdout`/`stderr` from the child process are captured in full and re-emitted through the logger
  (see Section 7) at `INFO` and `WARNING` level respectively, tagged with the job name and
  `execution_id` — never dropped silently — and persisted verbatim to the database (Section 8).
- On `subprocess.TimeoutExpired`, the process is killed, the event is logged at `ERROR` with
  status `timeout`, and the run counts as failed.
- On non-zero exit code, the event is logged at `ERROR` with status `failed` and the exit code.
- On success (`exit_code == 0`), logged at `INFO` with status `success` and duration.
- If `max_retries > 0`, failed runs are retried up to that many times with no backoff beyond the
  next scheduled tick unless `retry_delay_seconds` is set; retries are logged and stored as
  distinct `execution_id`s referencing the original `attempt` number.
- A `job_runs` row is written exactly once per execution, immediately when the run completes
  (success, failure, or timeout) — see Section 8 for the write path and failure handling.
- Scripts must not be imported into the scheduler's process — subprocess isolation ensures one
  script's crash, memory leak, or bad dependency cannot affect the scheduler or other jobs.

## 7. Logging Standard

`src/cron_runner/logging.py` provides plain, human-readable stdout logging (no structured/JSON
formatting):

```python
import logging
import sys

def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
        force=True,
    )

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
```

Every job-execution log line includes the job name, `execution_id`, status
(`started` | `success` | `failed` | `timeout` | `skipped_overlap`), and, on completion,
`duration_ms` and `exit_code`, e.g.:

```
2026-08-20 02:00:00 INFO cron_runner.job_runner job=nightly_report execution_id=... status=started attempt=1
2026-08-20 02:00:04 INFO cron_runner.job_runner job=nightly_report execution_id=... status=success exit_code=0 duration_ms=4211
```

All logs go to stdout/stderr only (container-native, consistent with the standard) — no
file-based logging inside the container. The database is the durable, queryable record of run
history; stdout logs are for live tailing and container log aggregation.

## 8. Database & Run History (SQLAlchemy + Alembic)

Every job execution's result and output are persisted to a `job_runs` table so history survives
restarts and is queryable independent of log retention.

`src/cron_runner/db.py` (standard engine module):

```python
from sqlalchemy import create_engine, text
from cron_runner.config import settings

_engine = None

def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
        )
    return _engine

def check_database() -> tuple[bool, str]:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:
        return False, f"database check failed: {type(exc).__name__}"
```

### 8.1 `job_runs` table

```python
# src/cron_runner/repository/models.py
from sqlalchemy import Column, String, Integer, DateTime, Text, Table, MetaData

metadata = MetaData(schema=None)  # table lives in a shared DB alongside other projects' tables

job_runs = Table(
    "cron_runner_job_runs",
    metadata,
    Column("id", String, primary_key=True),          # execution_id (UUID)
    Column("job_name", String, nullable=False, index=True),
    Column("attempt", Integer, nullable=False, default=1),
    Column("status", String, nullable=False),          # started|success|failed|timeout
    Column("exit_code", Integer, nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("duration_ms", Integer, nullable=True),
    Column("stdout", Text, nullable=True),
    Column("stderr", Text, nullable=True),
    Column("error_message", Text, nullable=True),
)
```

Rules:
- The table name is prefixed with `cron_runner_` specifically because the database is shared
  across multiple projects/services (see Section 8.2) — unprefixed generic names like `runs` or
  `jobs` are not permitted.
- A row is inserted at dispatch time with `status=started`, then updated in place at completion
  with final `status`, `exit_code`, `finished_at`, `duration_ms`, and full `stdout`/`stderr`.
- `stdout`/`stderr` are stored verbatim (no truncation) unless `CRON_MAX_OUTPUT_BYTES` is set, in
  which case output is truncated with a trailing `"...truncated"` marker.
- Writing to the database must never crash a job run: `repository/run_history.py` catches and
  logs (at `ERROR`) any DB write failure so a DB outage degrades to log-only history rather than
  losing the job's actual execution or blocking the scheduler.

### 8.2 Alembic Versioning for a Shared Database

This service's database may be shared with other, unrelated projects/services. To make that safe:

- Alembic's own bookkeeping table is renamed per-service via `alembic.ini`:
  ```ini
  [alembic]
  version_table = alembic_version_cron_runner
  ```
  This prevents collisions with other projects' migration histories in the same database (the
  standard's Section 9.2 "configure one service-specific version table name when sharing DB").
- All schema objects owned by this service are prefixed `cron_runner_` (tables, indexes,
  constraints), so `\dt` / migration diffs in a shared DB are unambiguous about ownership.
- Migrations only ever `CREATE`/`ALTER`/`DROP` `cron_runner_*` objects — a migration must never
  touch a table it does not prefix-own.
- `alembic/env.py` reads `DATABASE_URL` from the environment (shared connection string) and must
  set `version_table` to match `alembic.ini` for both offline and online migration modes.
- First migration (`alembic revision -m "create cron_runner_job_runs"`) creates the table from
  Section 8.1 with both `upgrade()` and `downgrade()`.
- Migrations run as a supervisor-managed `db-migrate` program at container startup, before the
  scheduler/API (see Section 10); if migrations fail, `db-migrate` exits non-zero and is not
  restarted (surfacing the failure in supervisor logs) rather than being silently retried.

## 9. API / Health Endpoints

Per the standard, a minimal Bottle + Waitress API runs alongside the worker (via supervisord) to
provide operational visibility. This service has no external HTTP business API — the API's sole
purpose is health/readiness.

- `GET /health` — process alive. Always `200 {"status": "ok", "service": "cron-runner-api"}`.
- `GET /ready` — returns `503` if:
  - the schedule file failed to load at startup, or
  - the scheduler loop heartbeat is older than `3 * poll_interval_seconds` (loop considered
    wedged/dead), or
  - the database is unreachable (via `check_database()`), or
  - the last config reload attempt failed validation (informational, does not block readiness by
    itself unless combined with a dead heartbeat).
  - Otherwise returns `200` with the currently loaded job count and last heartbeat age.

Error envelope and HTTP status mapping follow the standard's Section 7 exactly.

## 10. Container Process Model (supervisord)

Three programs managed by supervisord, all logging to stdout/stderr. `db-migrate` runs the
Alembic migration once at a lower `priority` (starts first) and does not `autorestart`; `api`
and `scheduler` start after it at a higher priority number:

```ini
[supervisord]
nodaemon=true
logfile=/dev/null
logfile_maxbytes=0

[program:db-migrate]
command=alembic upgrade head
directory=/app
priority=10
autostart=true
autorestart=false
startsecs=0
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0

[program:api]
command=python -m cron_runner.api_main
directory=/app
priority=20
autostart=true
autorestart=true
startretries=3
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0

[program:scheduler]
command=python -m cron_runner.workers.scheduler_worker
directory=/app
priority=20
autostart=true
autorestart=true
startretries=3
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
```

Note: supervisord `priority` only controls *start order*, not a wait-for-completion
dependency -- `api`/`scheduler` are started immediately after `db-migrate` is launched, not
after it exits. Migrations are expected to be fast; if a migration is slow enough to lose this
race, `/ready` still correctly reports `503` until the schema is in place.

## 11. Dockerfile / Compose

Dockerfile follows the standard's Section 12 baseline (non-root user, `HEALTHCHECK` against
`/health`, no `git clone`, local `COPY` build context, includes `scripts/`, `config/`, and
`alembic/`), with `CMD ["supervisord", "-c", "/app/supervisord.conf", "-n"]` -- the migration is
no longer run from the Dockerfile `CMD`; it is the `db-migrate` supervisor program above.

`docker-compose.yml` references the built image (per project convention) rather than a build
path, and includes only this service — no bundled dependency containers (the shared database is
assumed to already exist and is reached via `DATABASE_URL`):

```yaml
services:
  cron-runner:
    image: ghcr.io/<org>/cron-runner:latest
    environment:
      API_PORT: "8000"
      CRON_SCHEDULE_FILE: "/app/config/schedule.yaml"
      DATABASE_URL: "postgresql+psycopg://user:pass@shared-db-host:5432/shared_db"
    ports:
      - "8000:8000"
    volumes:
      - ./config/schedule.yaml:/app/config/schedule.yaml:ro
```

`.gitignore` excludes standard Python artifacts (`__pycache__/`, `*.pyc`, `.venv/`, `.pytest_cache/`,
`.mypy_cache/`, `.ruff_cache/`, `.env`).

## 12. Testing Standard

- `test_health.py`, `test_ready.py` — API happy/fail paths (readiness fails on dead heartbeat,
  DB unreachable, or load failure).
- `test_loader.py` — schedule file parsing/validation: valid file, invalid cron expression,
  duplicate job names, missing required fields.
- `test_matcher.py` — cron matching correctness at minute boundaries, including `*/N` and
  multi-field expressions.
- `test_job_runner.py` — success, non-zero exit, timeout-kill, output capture/log correlation,
  retry counting (subprocess calls mocked).
- `test_scheduler_worker.py` — overlap prevention (`allow_concurrent: false` skips a second
  dispatch while first is "running"), at-most-once-per-minute dispatch, per-job exception
  isolation (one job's crash doesn't stop the loop).
- `test_run_history.py` — `job_runs` row inserted on start and updated on completion with
  correct status/exit_code/output; DB write failure is caught and logged without failing the job.
- A migration smoke test (`alembic upgrade head` against a throwaway Postgres instance) is
  required in CI, per the standard's Section 16.1/17.

Baseline commands (unchanged from the standard): `pytest -q`, `ruff check .`, `mypy src`,
`bandit -r src`. Coverage gate: 80%.

## 13. Development Slices

The system is built and shipped in independently runnable/testable slices rather than all at
once. Each slice is a mergeable PR with its own passing tests; later slices only add to earlier
ones and never require reworking a prior slice's public behavior.

1. **Slice 1 — Schedule loading & matching (no execution yet)**
   - `config.py`, `scheduling/models.py`, `scheduling/loader.py`, `scheduling/matcher.py`.
   - Parse and validate `schedule.yaml`, validate cron expressions, unit-test minute matching.
   - No worker, no subprocess execution, no DB. Deliverable: a library that answers "which jobs
     are due right now" given a schedule file.

2. **Slice 2 — Job execution engine**
   - `workers/job_runner.py` + stdout logging (Section 7).
   - Run a single job as a subprocess with timeout, capture output, log start/success/failure.
   - Testable in isolation via `test_job_runner.py` with no scheduler loop involved.

3. **Slice 3 — Scheduler worker loop + overlap prevention**
   - `workers/scheduler_worker.py`, `workers/job_registry.py`, `__main__.py`.
   - Wire Slice 1 (what's due) into Slice 2 (run it), add the `JobRegistry` for
     `allow_concurrent: false` skip behavior and at-most-once-per-minute dispatch, add the
     heartbeat.
   - Deliverable: the container can run standalone (no API, no DB yet) and correctly execute
     jobs on schedule per stdout logs.

4. **Slice 4 — Health/ready API + supervisord**
   - `api/app.py`, `api/routes/health.py`, `supervisord.conf`, `Dockerfile`, `docker-compose.yml`.
   - `/health` and `/ready` initially only check heartbeat + config-load state (no DB check yet).
   - Deliverable: a deployable container image with operational health signals.

5. **Slice 5 — Database-backed run history + Alembic**
   - `db.py`, `repository/models.py`, `repository/run_history.py`, `alembic/`, `alembic.ini`.
   - Add `cron_runner_job_runs` writes (start + completion), the shared-DB versioning approach
     (Section 8.2), and extend `/ready` to check `check_database()`.
   - This slice is deliberately last because it is additive: Slices 1–4 fully function with
     log-only history, and Slice 5 layers durable history on top without changing scheduling or
     execution behavior.

6. **Slice 6 — Reliability hardening**
   - Config hot-reload (`CRON_CONFIG_RELOAD_SECONDS`), retries (`max_retries`), output truncation
     (`CRON_MAX_OUTPUT_BYTES`), CI migration smoke test, coverage gate enforcement.
   - Optional/iterative; can ship piecemeal after Slice 5 without blocking core functionality.

Rules:
- Each slice must leave `main` in a state where `docker compose up` starts successfully and
  existing tests pass — no slice depends on an unmerged future slice.
- Section numbers 1–8 above describe the *final* target design; slices describe the *order* in
  which that design is built.

## 14. Deviations From the Standard

- **Section 6 (Required API Endpoints)**: applied as-is (`/health`, `/ready`) even though there is
  no other business API, specifically to give the container a standard operational surface.
- **Section 9.2 (shared-DB version table)**: applied explicitly — `version_table` is renamed to
  `alembic_version_cron_runner` and all owned tables are prefixed `cron_runner_`, because this
  service's database is shared with other, unrelated projects (Section 8.2).
- **`.env.example`**: documents `CRON_SCHEDULE_FILE`, `CRON_POLL_INTERVAL_SECONDS`,
  `CRON_CONFIG_RELOAD_SECONDS`, `API_PORT`, `LOG_LEVEL`, and `DATABASE_URL`.
- **docker-compose.yml**: per repo convention, references a published image and does not bundle
  the database or any other dependency service (the shared database is managed outside this
  repo).

## 15. Definition of Done

A slice (Section 13) is done only if the checks relevant to what it introduces pass; the full
system is done only once all of the following hold:

1. `ruff`, `mypy`, `bandit`, and `pytest` pass.
2. `alembic upgrade head` succeeds against the shared database using the service-specific
   `alembic_version_cron_runner` version table, without altering any non-`cron_runner_` objects.
3. Docker image builds without cloning source in the Dockerfile.
4. `docker compose up` starts the container and `/health` returns `ok`; `/ready` returns `ok`
   once the schedule file loads, the database is reachable, and the scheduler heartbeat is fresh.
5. A job defined with a `* * * * *` schedule is observed to execute every minute in stdout logs
   and produces a corresponding `cron_runner_job_runs` row with `status=success`, exit code, and
   captured stdout/stderr.
6. A job with `allow_concurrent: false` and an execution time exceeding its own schedule interval
   produces `skipped_overlap` log lines instead of overlapping executions or duplicate DB rows.
7. An invalid `config/schedule.yaml` at startup causes the process to fail fast with a clear log
   message; an invalid reload leaves the prior valid schedule active.
8. A simulated database outage during a job run is caught, logged, and does not crash the
   scheduler or the job subprocess.

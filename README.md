# quant_monitoring

A containerized "cron container": a persistent scheduler worker process runs a set of
independent Python scripts on a schedule defined in [config/schedule.yaml](config/schedule.yaml),
prevents overlapping runs of the same job, and records every run's result to a shared database.

See [docs/specs/cron_container_spec.md](docs/specs/cron_container_spec.md) for the full spec.

## Local development

```bash
pip install .[dev]
cp .env.example .env   # edit DATABASE_URL to point at a local/shared Postgres

# apply migrations (creates cron_runner_job_runs using a service-specific version table)
alembic upgrade head

# run the scheduler worker (persistent, foreground)
python -m cron_runner

# run the health/ready API
python -m cron_runner.api_main
```

## Commands

```bash
pip install .[dev]     # install
ruff check .            # lint
mypy src                # typecheck
pytest -q               # test
alembic upgrade head    # migrate
docker build -t cron-runner:dev .   # build image
docker compose up --build           # run container
```

## Defining jobs

Edit [config/schedule.yaml](config/schedule.yaml). Each job needs a unique `name`, a `script`
path, a standard 5-field cron `schedule`, and a `timeout_seconds`. See
[docs/specs/cron_container_spec.md](docs/specs/cron_container_spec.md#3-schedule-definition-file)
for the full field reference.

The scheduler re-reads the file on `SIGHUP`, or automatically if `CRON_CONFIG_RELOAD_SECONDS` is
set to a nonzero value. An invalid file at startup is fatal; an invalid file at reload time is
logged and discarded, leaving the previous valid schedule active.

## Health & readiness

- `GET /health` — process alive.
- `GET /ready` — `200` once the schedule has loaded, the scheduler's heartbeat is fresh, and the
  database is reachable; `503` otherwise.

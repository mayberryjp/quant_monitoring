# Implementation Spec: `quant_monitoring` — API Rules & Alerting Engine

Date: 2026-07-07
Owner: Repo maintainer
Target repo: `mayberryjp/quant_monitoring`
Tracking issue: `#2`
Code standards source of truth: `mayberryjp/quant_symbols` (Python 3.12, `src/` layout, Bottle + waitress API, SQLAlchemy Core + Alembic, psycopg, Docker + supervisord)

---

## 1. Objective

Build a container-based **rules engine that monitors external REST/JSON APIs and alerts on conditions**.

The service periodically calls configured API endpoints, evaluates each response against a rule (a condition), records every check outcome (succeeded / failed / error) in Postgres, and raises a **Discord webhook** alert when a rule fails. Rules are stored in Postgres and are fully **CRUD-managed** (HTTP API + operator CLI). A **purge worker** deletes check-status history older than 30 days.

This document is written as **independent vertical slices**. Each slice has a goal, deliverables, code-level contracts, tests, and acceptance criteria so a developer can build and verify one slice at a time. Build slices in order; each is shippable and testable on its own.

---

## 2. Decisions & Assumptions

These fill gaps in the request. Change them only with a documented reason.

- **Language/runtime:** Python 3.12, `src/` layout, installed package name `quant-monitoring`, import package `quant_monitoring`. Mirrors `quant_symbols`.
- **Web framework:** `bottle` served by `waitress` (matches `quant_symbols`).
- **DB access:** SQLAlchemy **Core** with parameterized `text()` queries + `psycopg` driver. Alembic for migrations. No ORM models.
- **Outbound HTTP:** a thin transport wrapper over the standard-library `urllib` (mirrors `quant_symbols/vendors/massive/transport.py`) so tests inject a fake transport and no test performs live network I/O. `requests` is intentionally **not** added.
- **Alerting channel:** Discord incoming webhook (`DISCORD_WEBHOOK_URL`). Posts a JSON payload with an embed.
- **Rule "expected return":** modeled as a per-rule `expected` JSONB config object whose keys are interpreted by the evaluator selected via `check_type`.
- **Scheduling:** a single scheduler worker loop wakes every `SCHEDULER_TICK_SECONDS`, loads enabled rules, and runs each rule whose `interval_seconds` has elapsed since its last run.
- **Two initial check types** are shipped: `latest_status_succeeded` and `recent_record_exists` (defined in Slice 4).
- **Retention:** `check_results` and `alert_events` older than `RETENTION_DAYS` (default 30) are purged.
- **Secrets** (`DATABASE_URL`, `DISCORD_WEBHOOK_URL`) are never printed, logged, or `repr`'d. Only `.env.example` holds placeholders.

---

## 3. Proposed Repository Layout

```text
.
├── .dockerignore
├── .env.example
├── .gitignore
├── Dockerfile
├── README.md
├── alembic.ini
├── docker-compose.yml
├── pyproject.toml
├── supervisord.conf
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_monitoring_rules_and_results.py
├── src/
│   └── quant_monitoring/
│       ├── __init__.py
│       ├── cli.py                 # thin shim -> _cli_impl
│       ├── _cli_impl.py           # argparse subparsers + command funcs + main()
│       ├── config.py              # env-backed AppConfig/HttpConfig with from_env()
│       ├── db/
│       │   ├── __init__.py
│       │   └── engine.py          # create_engine helper (pool_pre_ping)
│       ├── http/
│       │   ├── __init__.py
│       │   ├── transport.py       # TransportResponse + UrllibTransport + FakeTransport
│       │   ├── client.py          # JsonHttpClient (retry/backoff/timeout)
│       │   └── errors.py          # typed HTTP errors
│       ├── rules/
│       │   ├── __init__.py
│       │   ├── models.py          # Rule, CheckResult, AlertEvent, CheckOutcome DTOs
│       │   ├── repository.py      # RuleRepository / CheckResultRepository / AlertEventRepository
│       │   ├── evaluators.py      # evaluator registry + latest_status_succeeded + recent_record_exists
│       │   ├── runner.py          # CheckRunner: fetch -> evaluate -> persist -> alert
│       │   └── scheduler.py       # SchedulerLoop (run_once / run_forever)
│       ├── alerts/
│       │   ├── __init__.py
│       │   ├── discord.py         # DiscordWebhookClient + AlertPolicy
│       │   └── errors.py
│       ├── maintenance/
│       │   ├── __init__.py
│       │   └── purge.py           # PurgeJob (+ PurgeSummary.format_line())
│       └── api/
│           ├── __init__.py
│           ├── app.py             # Bottle app + waitress entrypoint (python3 -m quant_monitoring.api.app)
│           └── schemas.py         # request validation for rule payloads
└── tests/
    ├── fixtures/
    │   └── api_responses/
    │       ├── latest_succeeded.json
    │       ├── latest_failed.json
    │       ├── recent_within_24h.json
    │       ├── stale_no_recent.json
    │       └── empty_records.json
    ├── test_config.py
    ├── test_http_client.py
    ├── test_evaluators.py
    ├── test_repository.py
    ├── test_runner.py
    ├── test_discord_alerter.py
    ├── test_purge.py
    ├── test_api_rules_crud.py
    └── test_schema_contract.py
```

---

## 4. Module Boundaries

- `quant_monitoring.http` — outbound request execution only: timeout, retry/backoff, rate-limit handling, typed errors, JSON parsing. Must not import DB, rules, or alert code.
- `quant_monitoring.rules.evaluators` — **pure** functions: given a decoded payload + `expected` config, return a `CheckOutcome`. No network, no DB, no clock other than an injected `now`.
- `quant_monitoring.rules.repository` — all SQL. Parameterized `text()` only. No business logic.
- `quant_monitoring.rules.runner` — orchestration: use `JsonHttpClient` to fetch, an evaluator to decide, repositories to persist, `AlertPolicy` + `DiscordWebhookClient` to notify.
- `quant_monitoring.rules.scheduler` — timing/dispatch only; delegates each rule to the runner.
- `quant_monitoring.alerts` — Discord formatting/delivery + alert suppression policy. No DB writes except via injected repositories.
- `quant_monitoring.maintenance` — retention/purge only.
- `quant_monitoring.api` — Bottle HTTP surface for rule CRUD; delegates to repositories; no evaluation logic.

---

## 5. Configuration Contract (`config.py`, `.env.example`)

Environment-backed config with `from_env()` classmethods. Missing `DATABASE_URL` falls back to the local dev DSN. Secrets are never printed or included in `repr`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `POSTGRES_HOST` | `localhost` | local compose Postgres |
| `POSTGRES_PORT` | `5432` | |
| `POSTGRES_DB` | `quant` | |
| `POSTGRES_USER` | `quant` | |
| `POSTGRES_PASSWORD` | `quant_dev_password` | dev only |
| `DATABASE_URL` | `postgresql+psycopg://quant:quant_dev_password@localhost:5432/quant` | SQLAlchemy DSN |
| `DISCORD_WEBHOOK_URL` | *(empty)* | alert destination; empty disables delivery (logs instead) |
| `HTTP_TIMEOUT_SECONDS` | `30` | default per-request timeout |
| `HTTP_RETRY_COUNT` | `3` | retryable attempts |
| `HTTP_BACKOFF_SECONDS` | `0.5` | base backoff |
| `HTTP_BACKOFF_MULTIPLIER` | `2` | exponential factor |
| `SCHEDULER_TICK_SECONDS` | `15` | scheduler wake interval |
| `PURGE_INTERVAL_SECONDS` | `86400` | purge worker cadence |
| `RETENTION_DAYS` | `30` | status history retention |
| `ALERT_COOLDOWN_SECONDS` | `3600` | default re-alert suppression window (per-rule override allowed) |
| `API_HOST` | `0.0.0.0` | Bottle/waitress bind host |
| `API_PORT` | `8000` | Bottle/waitress bind port |

Rules:
- Missing `DISCORD_WEBHOOK_URL` must not crash the scheduler; delivery is skipped and logged at WARNING, and the `alert_events` row is recorded with `delivered=false`.
- Do not print or `repr` `DATABASE_URL` or `DISCORD_WEBHOOK_URL`.

---

## 6. Data Model (Postgres schema `monitoring`)

One Alembic revision `0001_monitoring_rules_and_results` creates schema `monitoring` and three tables.

### `monitoring.rules`
| Column | Type | Notes |
| --- | --- | --- |
| `id` | BIGSERIAL PK | |
| `name` | TEXT NOT NULL UNIQUE | human label |
| `description` | TEXT NULL | |
| `enabled` | BOOLEAN NOT NULL DEFAULT true | scheduler only runs enabled rules |
| `check_type` | TEXT NOT NULL | must exist in evaluator registry |
| `endpoint_url` | TEXT NOT NULL | API to poll |
| `http_method` | TEXT NOT NULL DEFAULT 'GET' | `GET` or `POST` |
| `request_headers` | JSONB NOT NULL DEFAULT '{}' | sent with request |
| `request_body` | JSONB NULL | for `POST` |
| `expected` | JSONB NOT NULL | evaluator config (see Slice 4) |
| `interval_seconds` | INTEGER NOT NULL | run cadence; `> 0` |
| `timeout_seconds` | INTEGER NOT NULL DEFAULT 30 | per-request override |
| `consecutive_failures_to_alert` | INTEGER NOT NULL DEFAULT 1 | alert threshold |
| `alert_cooldown_seconds` | INTEGER NOT NULL DEFAULT 3600 | per-rule cooldown |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |

### `monitoring.check_results`
| Column | Type | Notes |
| --- | --- | --- |
| `id` | BIGSERIAL PK | |
| `rule_id` | BIGINT NOT NULL REFERENCES monitoring.rules(id) ON DELETE CASCADE | |
| `status` | TEXT NOT NULL | `succeeded` \| `failed` \| `error` |
| `detail` | TEXT NULL | reason string |
| `observed` | JSONB NULL | e.g. `{"latest_status":"Succeeded","latest_timestamp":"..."}` |
| `http_status` | INTEGER NULL | upstream response code |
| `latency_ms` | INTEGER NULL | request duration |
| `started_at` | TIMESTAMPTZ NOT NULL | |
| `finished_at` | TIMESTAMPTZ NOT NULL | |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | purge key |

Indexes: `(rule_id, created_at DESC)`, `(created_at)`.

### `monitoring.alert_events`
| Column | Type | Notes |
| --- | --- | --- |
| `id` | BIGSERIAL PK | |
| `rule_id` | BIGINT NOT NULL REFERENCES monitoring.rules(id) ON DELETE CASCADE | |
| `check_result_id` | BIGINT NULL REFERENCES monitoring.check_results(id) ON DELETE SET NULL | |
| `kind` | TEXT NOT NULL | `failure` \| `recovery` |
| `status_code` | INTEGER NULL | Discord HTTP status |
| `delivered` | BOOLEAN NOT NULL DEFAULT false | |
| `error` | TEXT NULL | delivery error |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | purge key |

Indexes: `(rule_id, created_at DESC)`, `(created_at)`.

`db verify` asserts schema version + table count + seeded rule count. Expected line:
```text
postgres=ok schema_version=0001_monitoring_rules_and_results tables=3 rules=2
```

---

## 7. Check Type Contracts

`expected` JSONB is interpreted per `check_type`. Records are extracted from the decoded JSON via `records_path` (dotted key path to a JSON array; empty/omitted means the top-level value is the array).

### `latest_status_succeeded`
Confirms the **most recent** record carries a success status.
```json
{
  "records_path": "data",
  "timestamp_field": "completedAt",
  "status_field": "status",
  "expected_status": "Succeeded"
}
```
Behavior: extract array; if empty → `failed` ("no records"). Pick the record with the max parsed `timestamp_field`. `succeeded` iff `status_field == expected_status` (exact, case-sensitive), else `failed`. `observed` includes `latest_status` and `latest_timestamp`.

### `recent_record_exists`
Confirms at least one record is recent (freshness/heartbeat).
```json
{
  "records_path": "data",
  "timestamp_field": "createdAt",
  "max_age_seconds": 86400
}
```
Behavior: extract array; `succeeded` iff any record's parsed `timestamp_field` is `>= now_utc - max_age_seconds`, else `failed`. `observed` includes `newest_timestamp` and `record_count`.

Timestamp parsing helper supports ISO-8601 (incl. trailing `Z`) and integer epoch seconds/milliseconds. Unparseable timestamps are skipped and counted in `observed.skipped_timestamps`.

---

# Slices

Each slice: **Goal → Deliverables → Contracts → Tests → Acceptance**. All modules begin with `from __future__ import annotations` and use full type hints. Follow `quant_symbols` conventions throughout.

## Slice 0 — Repo & Infrastructure Foundation

**Goal:** Buildable, installable package with Docker + supervisord skeleton and a working DB CLI, mirroring `quant_symbols`.

**Deliverables:**
- `pyproject.toml` (setuptools>=69, `requires-python>=3.12`, deps: `alembic>=1.13`, `bottle>=0.13`, `psycopg[binary]>=3.1`, `SQLAlchemy>=2.0`, `waitress>=3.0`; `dev`: `pytest>=8`, `webtest>=3.0`; `[tool.setuptools.packages.find] where=["src"]`; pytest `testpaths=["tests"]`, `pythonpath=["src"]`).
- `Dockerfile` (`python:3.12-slim`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`, `WORKDIR /app`, install `bash ca-certificates git procps`, `git clone` this repo, `pip install -e ".[dev]"` + `supervisor`, env `API_PORT=8000 SCHEDULER_TICK_SECONDS=15 PURGE_INTERVAL_SECONDS=86400 RETENTION_DAYS=30`, `CMD ["supervisord","-c","/app/supervisord.conf"]`).
- `docker-compose.yml` with `postgres:16` (healthcheck) + `quant_monitoring` service.
- `supervisord.conf` with the `db-migrate` program only (workers added in later slices).
- `.env.example`, `.dockerignore`, `.gitignore`, `alembic.ini`, `alembic/` scaffold (`env.py` reads `DATABASE_URL`).
- `src/quant_monitoring/{__init__.py,cli.py,_cli_impl.py}`, `db/engine.py`.

**Contracts:**
- `cli.py`: thin shim — `from quant_monitoring._cli_impl import *` + `from quant_monitoring._cli_impl import main`, `if __name__ == "__main__": sys.exit(main())`.
- `_cli_impl.py`: `build_parser()`, `main(argv: list[str] | None = None) -> int`, `logging.basicConfig(format="%(asctime)s %(levelname)-8s %(name)s  %(message)s", datefmt="%H:%M:%S", level=logging.INFO)`. Commands: `db upgrade`, `db verify`, `db downgrade-base`.
- `db/engine.py`: `def create_db_engine(url: str | None = None): return create_engine(url or _database_url(), pool_pre_ping=True)`.

**Tests:** package imports; `python3 -m quant_monitoring.cli --help` exits 0.

**Acceptance:**
```bash
docker compose up -d postgres
python3 -m quant_monitoring.cli db upgrade   # no-op until Slice 1
docker build -t quant-monitoring:dev .
docker run --rm quant-monitoring:dev python3 --version   # Python 3.12.x
docker run --rm quant-monitoring:dev python3 -m pytest -q
```

## Slice 1 — Database Schema & Seed

**Goal:** Alembic revision `0001_monitoring_rules_and_results` creating schema `monitoring` + the three tables (Section 6) with indexes, and `db verify`.

**Deliverables:**
- `alembic/versions/0001_monitoring_rules_and_results.py` (`upgrade()` + `downgrade()` dropping tables then schema).
- Seed **two disabled example rules** (Section 7) with placeholder `endpoint_url`s so operators fill real URLs via CRUD:
  1. `example-latest-succeeded` — `check_type=latest_status_succeeded`, `interval_seconds=300`.
  2. `example-recent-24h` — `check_type=recent_record_exists`, `expected.max_age_seconds=86400`, `interval_seconds=300`.
- `db verify` in `_cli_impl.py` asserting schema version, `tables=3`, and rule count.

**Contracts:** `db verify` prints exactly `postgres=ok schema_version=0001_monitoring_rules_and_results tables=3 rules=2` and exits non-zero on mismatch (`raise SystemExit(...)`).

**Tests:** `test_schema_contract.py` — upgrade → verify → downgrade-base → upgrade → verify is idempotent; three tables exist under `monitoring`; two seed rows present and `enabled=false`.

**Acceptance:**
```bash
python3 -m quant_monitoring.cli db upgrade
python3 -m quant_monitoring.cli db verify
python3 -m quant_monitoring.cli db downgrade-base
python3 -m quant_monitoring.cli db upgrade
```

## Slice 2 — Config & HTTP Client

**Goal:** Env-backed config + a resilient JSON HTTP client with an injectable transport.

**Deliverables:** `config.py`, `http/transport.py`, `http/client.py`, `http/errors.py`.

**Contracts:**
```python
@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes

class Transport(Protocol):
    def send(self, *, method: str, url: str, headers: Mapping[str, str],
             body: bytes | None, timeout_seconds: float) -> TransportResponse: ...

@dataclass(frozen=True)
class JsonResponse:
    status_code: int
    headers: Mapping[str, str]
    json: Any
    latency_ms: int

class JsonHttpClient:
    def __init__(self, transport: Transport, config: HttpConfig) -> None: ...
    def request_json(self, *, method: str, url: str,
                     headers: Mapping[str, str] | None = None,
                     body: Any | None = None,
                     timeout_seconds: float | None = None) -> JsonResponse: ...
```
- Retryable status: `408, 425, 429, 500, 502, 503, 504` + connection/timeout errors. Non-retryable `4xx` raise `HttpStatusError`.
- Exponential backoff (`HTTP_BACKOFF_SECONDS * HTTP_BACKOFF_MULTIPLIER**attempt`); honor `Retry-After` on `429`.
- Typed errors in `http/errors.py`: `HttpError` (base), `HttpTimeoutError`, `HttpConnectionError`, `HttpStatusError` (has `status_code`), `RateLimitError` (has `retry_after_seconds`), `InvalidJsonError`.
- `UrllibTransport` (real) + `FakeTransport` (queue of scripted `TransportResponse`/exceptions) for tests.

**Tests:** `test_config.py`, `test_http_client.py` — defaults; single success; retry on `500` then success; `429` honors `Retry-After`; non-retryable `401` raises; timeout raises typed error; invalid JSON raises `InvalidJsonError`. No live network in any test.

**Acceptance:** `python3 -m pytest -q tests/test_http_client.py tests/test_config.py` passes.

## Slice 3 — Domain Models & Repositories

**Goal:** DTOs + all SQL persistence for rules, results, and alert events.

**Deliverables:** `rules/models.py`, `rules/repository.py`.

**Contracts:**
```python
@dataclass(frozen=True)
class Rule:
    id: int
    name: str
    description: str | None
    enabled: bool
    check_type: str
    endpoint_url: str
    http_method: str
    request_headers: dict[str, Any]
    request_body: dict[str, Any] | None
    expected: dict[str, Any]
    interval_seconds: int
    timeout_seconds: int
    consecutive_failures_to_alert: int
    alert_cooldown_seconds: int

class RuleRepository:
    def __init__(self, engine) -> None: ...
    def list_all(self) -> list[Rule]: ...
    def list_enabled(self) -> list[Rule]: ...
    def get(self, rule_id: int) -> Rule | None: ...
    def create(self, data: Mapping[str, Any]) -> Rule: ...
    def update(self, rule_id: int, data: Mapping[str, Any]) -> Rule | None: ...
    def delete(self, rule_id: int) -> bool: ...
    def set_enabled(self, rule_id: int, enabled: bool) -> Rule | None: ...

class CheckResultRepository:
    def insert(self, data: Mapping[str, Any]) -> int: ...
    def latest_for_rule(self, rule_id: int) -> CheckResult | None: ...
    def recent_for_rule(self, rule_id: int, limit: int = 50) -> list[CheckResult]: ...
    def purge_older_than(self, cutoff: datetime) -> int: ...

class AlertEventRepository:
    def insert(self, data: Mapping[str, Any]) -> int: ...
    def last_for_rule(self, rule_id: int, kind: str | None = None) -> AlertEvent | None: ...
    def purge_older_than(self, cutoff: datetime) -> int: ...
```
- All SQL uses parameterized `text()` (no string interpolation of values). JSONB columns round-trip as Python dicts.
- `update()` sets `updated_at = now()`; unknown `id` returns `None`.

**Tests:** `test_repository.py` against local Postgres — CRUD round-trip; enable/disable; `ON DELETE CASCADE`; JSONB fidelity; `purge_older_than` deletes only rows older than cutoff and returns the count.

**Acceptance:** `python3 -m pytest -q tests/test_repository.py` passes against `docker compose` Postgres.

## Slice 4 — Evaluators (Rules Engine Core)

**Goal:** Pure evaluators for the two check types + a registry.

**Deliverables:** `rules/evaluators.py` + fixtures under `tests/fixtures/api_responses/`.

**Contracts:**
```python
@dataclass(frozen=True)
class CheckOutcome:
    status: str            # "succeeded" | "failed"
    detail: str
    observed: dict[str, Any]

class Evaluator(Protocol):
    check_type: str
    def evaluate(self, payload: Any, config: Mapping[str, Any], *, now: datetime) -> CheckOutcome: ...

EVALUATORS: dict[str, Evaluator]   # keyed by check_type

def extract_records(payload: Any, records_path: str) -> list[Any]: ...
def parse_timestamp(value: Any) -> datetime | None: ...   # ISO-8601 (+Z) or epoch s/ms -> aware UTC
```
- `latest_status_succeeded` and `recent_record_exists` per Section 7. Evaluators never do I/O; `now` is injected. Missing/empty records or missing configured fields → `failed` with a clear `detail` (not an exception).
- Config validation errors (unknown `check_type`, missing required keys) raise `EvaluatorConfigError`; used by API validation in Slice 8.

**Tests:** `test_evaluators.py` — latest record succeeded/failed; empty array → failed; recent within 24h → succeeded; stale → failed; epoch and ISO timestamps; unparseable timestamps skipped; nested `records_path`.

**Acceptance:** `python3 -m pytest -q tests/test_evaluators.py` passes with zero I/O.

## Slice 5 — Discord Alerter & Alert Policy

**Goal:** Deliver alerts to Discord and decide *when* to alert.

**Deliverables:** `alerts/discord.py`, `alerts/errors.py`.

**Contracts:**
```python
@dataclass(frozen=True)
class DiscordSendResult:
    delivered: bool
    status_code: int | None
    error: str | None

class DiscordWebhookClient:
    def __init__(self, webhook_url: str | None, http: JsonHttpClient) -> None: ...
    def send_failure(self, rule: Rule, outcome: CheckOutcome, *, http_status: int | None) -> DiscordSendResult: ...
    def send_recovery(self, rule: Rule, outcome: CheckOutcome) -> DiscordSendResult: ...

class AlertPolicy:
    def should_alert_failure(self, rule: Rule, recent: list[CheckResult],
                             last_alert: AlertEvent | None, *, now: datetime) -> bool: ...
    def should_alert_recovery(self, rule: Rule, last_alert: AlertEvent | None) -> bool: ...
```
- Failure alert fires when consecutive failures `>= consecutive_failures_to_alert` **and** no `failure` alert within `alert_cooldown_seconds`.
- Recovery alert fires once when a rule returns to `succeeded` after an alerted failure.
- Empty/missing `DISCORD_WEBHOOK_URL` → `DiscordSendResult(delivered=False, ...)`, logged WARNING, never raises. Webhook URL never logged.
- Discord payload: an embed with rule name, status, `detail`, endpoint host (not full URL with secrets), observed values, timestamp; failure=red, recovery=green.

**Tests:** `test_discord_alerter.py` with `FakeTransport` — payload shape; cooldown suppression; consecutive-failure threshold; recovery once; disabled webhook returns not-delivered without raising; URL never appears in logs.

**Acceptance:** `python3 -m pytest -q tests/test_discord_alerter.py` passes, no live calls.

## Slice 6 — Check Runner & Scheduler Worker

**Goal:** End-to-end single-rule execution + a scheduler loop; wire the scheduler into supervisord.

**Deliverables:** `rules/runner.py`, `rules/scheduler.py`, `checks` CLI command family, `[program:scheduler]` in `supervisord.conf`.

**Contracts:**
```python
class CheckRunner:
    def __init__(self, engine, http: JsonHttpClient, alerter: DiscordWebhookClient,
                 policy: AlertPolicy) -> None: ...
    def run_rule(self, rule: Rule, *, now: datetime | None = None) -> CheckResult: ...
    # fetch endpoint -> evaluate -> insert check_results -> maybe alert + insert alert_events.
    # Network/JSON failure -> status="error" (still recorded); errors count toward alerting.

class SchedulerLoop:
    def __init__(self, engine, runner: CheckRunner) -> None: ...
    def run_once(self, *, now: datetime | None = None) -> RunSummary: ...   # run all currently-due enabled rules
    def run_forever(self, tick_seconds: int) -> None: ...
```
- Due rule: `latest_for_rule is None` or `now - latest.started_at >= interval_seconds`. Track next-due in memory to avoid DB hammering.
- `RunSummary.format_line()` → `scheduler_run=ok due=<n> succeeded=<n> failed=<n> error=<n>`.
- CLI: `checks run --once`, `checks run --schedule SECONDS`, `checks run-rule --id N`.

**supervisord:**
```ini
[program:scheduler]
command=bash -c "sleep 5 && exec python3 -m quant_monitoring.cli checks run --schedule %(ENV_SCHEDULER_TICK_SECONDS)s"
directory=/app
autostart=true
autorestart=true
startretries=999
priority=10
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
environment=PYTHONUNBUFFERED="1"
```

**Tests:** `test_runner.py` with `FakeTransport` + Postgres — succeeded path records `succeeded`; failing evaluator records `failed` and triggers alert per policy; network error records `error`; recovery emitted; `run_once` runs only due rules.

**Acceptance:**
```bash
python3 -m quant_monitoring.cli checks run --once   # prints scheduler_run=ok ...
```

## Slice 7 — Purge Worker

**Goal:** Delete status history older than `RETENTION_DAYS`; wire purge worker into supervisord.

**Deliverables:** `maintenance/purge.py`, `maintenance` CLI command family, `[program:purge]` in `supervisord.conf`.

**Contracts:**
```python
@dataclass(frozen=True)
class PurgeSummary:
    check_results_deleted: int
    alert_events_deleted: int
    retention_days: int
    def format_line(self) -> str: ...   # "purge=ok retention_days=30 check_results=<n> alert_events=<n>"

class PurgeJob:
    def __init__(self, engine) -> None: ...
    def run(self, *, retention_days: int, now: datetime | None = None) -> PurgeSummary: ...
```
- Deletes `check_results` and `alert_events` with `created_at < now_utc - retention_days`.
- CLI: `maintenance purge --once [--retention-days N]`, `maintenance purge --schedule SECONDS [--retention-days N]` (default from `RETENTION_DAYS`).

**supervisord:**
```ini
[program:purge]
command=bash -c "sleep 5 && exec python3 -m quant_monitoring.cli maintenance purge --schedule %(ENV_PURGE_INTERVAL_SECONDS)s"
directory=/app
autostart=true
autorestart=true
startretries=999
priority=10
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
environment=PYTHONUNBUFFERED="1"
```

**Tests:** `test_purge.py` — rows older than cutoff deleted, newer retained; returns/prints correct counts; safe on empty tables.

**Acceptance:**
```bash
python3 -m quant_monitoring.cli maintenance purge --once --retention-days 30   # prints purge=ok ...
```

## Slice 8 — Rule CRUD API (Bottle + waitress)

**Goal:** HTTP surface to manage rules and inspect recent results; wire API into supervisord.

**Deliverables:** `api/app.py`, `api/schemas.py`, `[program:api]` in `supervisord.conf`.

**Endpoints (JSON):**
| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/healthz` | `{"status":"ok"}` |
| GET | `/rules` | list all rules |
| POST | `/rules` | validate + create → 201 |
| GET | `/rules/<id:int>` | one rule or 404 |
| PUT | `/rules/<id:int>` | validate + update or 404 |
| DELETE | `/rules/<id:int>` | 204 or 404 |
| GET | `/rules/<id:int>/results?limit=50` | recent `check_results` |

**Validation (`schemas.py`):** `name` non-empty & unique; `check_type` in `EVALUATORS`; `http_method` in {`GET`,`POST`}; `interval_seconds > 0`; `timeout_seconds > 0`; `expected` validated by the evaluator's config validator (Slice 4). Invalid → `400` with `{"error": "..."}`. Duplicate name → `409`.

**Entrypoint:** `python3 -m quant_monitoring.api.app` serves the Bottle app via `waitress.serve(app, host=API_HOST, port=API_PORT)`.

**supervisord:**
```ini
[program:api]
command=bash -c "sleep 5 && exec python3 -m quant_monitoring.api.app"
directory=/app
autostart=true
autorestart=true
startretries=999
priority=10
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
environment=PYTHONUNBUFFERED="1"
```

**Tests:** `test_api_rules_crud.py` with `webtest` — full CRUD lifecycle; validation rejects bad `check_type`/`interval`/`expected`; duplicate name → 409; unknown id → 404; `/healthz` ok. No live network.

**Acceptance:** `python3 -m pytest -q tests/test_api_rules_crud.py` passes; `curl :8000/healthz` returns ok in-container.

---

## 8. supervisord Orchestration (final)

Program start order via `priority`: `db-migrate` (priority 1, `autorestart=false`, `exitcodes=0`) runs `alembic upgrade head` first; then `scheduler`, `purge`, `api` (priority 10, `autorestart=true`, `startretries=999`, each `sleep 5 && exec ...`). All log to `/dev/stdout` + `/dev/stderr` with `PYTHONUNBUFFERED=1`.

```ini
[program:db-migrate]
command=alembic upgrade head
directory=/app
autostart=true
autorestart=false
startsecs=0
exitcodes=0
priority=1
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
environment=PYTHONUNBUFFERED="1"
```

---

## 9. End-to-End Smoke (full stack)

```bash
cp .env.example .env
docker compose up -d postgres
python3 -m quant_monitoring.cli db upgrade
python3 -m quant_monitoring.cli db verify
# postgres=ok schema_version=0001_monitoring_rules_and_results tables=3 rules=2
python3 -m quant_monitoring.cli checks run --once
python3 -m quant_monitoring.cli maintenance purge --once --retention-days 30
docker build -t quant-monitoring:dev .
docker run --rm quant-monitoring:dev python3 -m pytest -q
```

---

## 10. Coding Standards (inherited from `quant_symbols`)

- Every module: `from __future__ import annotations`; full type hints; `X | None` unions.
- Module constants `UPPER_SNAKE_CASE`; private helpers prefixed `_`.
- Thin `cli.py` shim; real logic in `_cli_impl.py`; argparse subparsers with `set_defaults(func=...)`; `main(argv)->int`.
- SQLAlchemy Core + parameterized `text()`; `create_engine(url, pool_pre_ping=True)`; `.scalar_one()` / `.scalars().all()`.
- CLI errors via `raise SystemExit("message")`; status output as single `key=value` lines; summaries expose `.format_line()`.
- Worker loops support `--schedule SECONDS`; run-once path returns non-zero on failure.
- Never print/log/`repr` secrets (`DATABASE_URL`, `DISCORD_WEBHOOK_URL`, `request_headers` auth values).
- Tests use `pytest`; outbound HTTP uses `FakeTransport`; **no default test performs live network I/O**.
- Lazy-import optional/heavy deps inside functions where `quant_symbols` does.

---

## 11. Security Notes

- All DB writes/reads use parameterized `text()` — no value interpolation (prevents SQL injection). Rule-provided `records_path`/field names only index decoded JSON in Python; they never touch SQL.
- Outbound requests use rule-configured URLs/headers; document that operators must only configure trusted endpoints (SSRF surface is inherent to the product and is operator-controlled).
- Redact secrets in Discord embeds and logs (endpoint host only, not full URL with query/token).

---

## 12. Out of Scope (v1)

- Auth/RBAC on the CRUD API.
- Additional check types beyond the two shipped (extension point exists via `EVALUATORS`).
- Alert channels other than Discord.
- UI/frontend.
- Distributed scheduling / multiple scheduler replicas (single scheduler assumed).
- Per-rule secret storage/rotation.

---

## 13. Sequencing Notes

1. Slice 0 (foundation) → 1 (schema) must land first.
2. Slice 2 (config/HTTP) and Slice 3 (models/repos) can proceed in parallel after Slice 1.
3. Slice 4 (evaluators) depends only on fixtures; can start any time after Slice 0.
4. Slice 5 (alerter) depends on Slice 2 + Slice 3.
5. Slice 6 (runner/scheduler) depends on Slices 2–5.
6. Slice 7 (purge) depends on Slice 3.
7. Slice 8 (API) depends on Slices 3 + 4.
8. Add each worker to `supervisord.conf` in the slice that introduces it.

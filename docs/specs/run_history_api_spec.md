# Spec: Run History Read API (for frontend consumption)

## 1. Purpose

Give a frontend a stable, read-only HTTP surface over the `cron_runner_job_runs` table
(written by the scheduler per [cron_container_spec.md](cron_container_spec.md#8-database--run-history-sqlalchemy--alembic))
and the currently loaded job definitions, so a UI can show job run history, statuses,
durations, and captured output without touching Postgres directly.

These routes are added to the existing Bottle app (`src/cron_runner/api/app.py`),
served alongside `/health` and `/ready` on `API_PORT`. They are read-only — no endpoint
triggers, cancels, or modifies a run.

## 2. Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/jobs` | List all jobs currently loaded from `schedule.yaml` |
| GET | `/runs` | List run history, newest first, with filters + pagination |
| GET | `/runs/{execution_id}` | A single run, including full `stdout`/`stderr` |
| GET | `/jobs/{job_name}/runs/latest` | The most recent run for one job |

### 2.1 `GET /jobs`

Returns the jobs currently loaded by the scheduler (from in-memory schedule state, not
the database) so the frontend can build a job picker without needing DB access for
static metadata.

**Response 200**
```json
{
  "status": "ok",
  "count": 2,
  "jobs": [
    {
      "name": "daily_ingestion_report",
      "schedule": "0 16 * * *",
      "timezone": "Asia/Tokyo",
      "enabled": true,
      "allow_concurrent": false,
      "timeout_seconds": 300,
      "max_retries": 1
    }
  ]
}
```

### 2.2 `GET /runs`

**Query parameters**

| Param | Type | Default | Notes |
|---|---|---|---|
| `job_name` | string | — | exact match filter |
| `status` | string | — | one of `started`, `success`, `failed`, `timeout` |
| `from` | ISO 8601 datetime | — | `started_at >= from` |
| `to` | ISO 8601 datetime | — | `started_at <= to` |
| `limit` | int | `50` | max `200` |
| `offset` | int | `0` | |

Invalid `status` or out-of-range `limit` returns `422` with the standard error envelope.

Rows are ordered by `started_at DESC`. `stdout`/`stderr` are **omitted** from list
responses (they can be large); fetch `/runs/{execution_id}` for full output.

**Response 200**
```json
{
  "status": "ok",
  "count": 2,
  "limit": 50,
  "offset": 0,
  "results": [
    {
      "execution_id": "5b1e...",
      "job_name": "daily_ingestion_report",
      "attempt": 1,
      "status": "success",
      "exit_code": 0,
      "started_at": "2026-08-20T07:00:00+00:00",
      "finished_at": "2026-08-20T07:00:04+00:00",
      "duration_ms": 4211,
      "error_message": null
    }
  ]
}
```

### 2.3 `GET /runs/{execution_id}`

Returns one run including full `stdout` and `stderr`. Unknown `execution_id` returns
`404` with `{"status": "error", "code": "not_found", "error": "run not found"}`.

**Response 200**
```json
{
  "status": "ok",
  "run": {
    "execution_id": "5b1e...",
    "job_name": "daily_ingestion_report",
    "attempt": 1,
    "status": "success",
    "exit_code": 0,
    "started_at": "2026-08-20T07:00:00+00:00",
    "finished_at": "2026-08-20T07:00:04+00:00",
    "duration_ms": 4211,
    "stdout": "raw_latest_runs={...}\nsummary_generated ok\nposted_to_discord ok\n",
    "stderr": "",
    "error_message": null
  }
}
```

### 2.4 `GET /jobs/{job_name}/runs/latest`

Convenience endpoint equivalent to `/runs?job_name={job_name}&limit=1`, returning a
single run object (not wrapped in a list) or `404` if the job has never run.

**Response 200**
```json
{"status": "ok", "run": { "...": "same shape as 2.3" }}
```

**Response 404**
```json
{"status": "error", "code": "not_found", "error": "no runs found for job 'daily_ingestion_report'"}
```

## 3. Field Reference

Mirrors the `cron_runner_job_runs` table columns 1:1 except `id` is renamed
`execution_id` in JSON responses for clarity:

| JSON field | DB column | Type |
|---|---|---|
| `execution_id` | `id` | string (UUID) |
| `job_name` | `job_name` | string |
| `attempt` | `attempt` | int |
| `status` | `status` | `started` \| `success` \| `failed` \| `timeout` |
| `exit_code` | `exit_code` | int or `null` |
| `started_at` | `started_at` | ISO 8601 datetime (UTC) |
| `finished_at` | `finished_at` | ISO 8601 datetime or `null` (null while `status=started`) |
| `duration_ms` | `duration_ms` | int or `null` |
| `stdout` | `stdout` | string (detail endpoint only) |
| `stderr` | `stderr` | string (detail endpoint only) |
| `error_message` | `error_message` | string or `null` |

## 4. Error Envelope

Follows `BACKEND_CODING_STANDARDS.md` section 7 exactly:

```json
{"status": "error", "code": "validation_error", "error": "...", "detail": "..."}
```

| HTTP | `code` | When |
|---|---|---|
| `404` | `not_found` | unknown `execution_id`, or no runs for `job_name` |
| `422` | `validation_error` | invalid `status`, `limit`, `offset`, or datetime filters |
| `500` | `internal_error` | unexpected DB error (message must not leak credentials) |
| `503` | `not_ready` | DB unreachable (mirrors `/ready`) |

## 5. Implementation Notes (backend)

- New module `src/cron_runner/api/routes/runs.py`, registered in `api/app.py` alongside
  `register_health_routes`.
- Query layer lives in `src/cron_runner/repository/run_history.py` (extend with
  `list_runs(...)`, `get_run(execution_id)`, `get_latest_run(job_name)` read functions
  next to the existing `record_start`/`record_completion` writers).
- `/jobs` reads from the scheduler's currently-loaded `ScheduleFile` (needs the same
  cross-process consideration as the heartbeat in
  [cron_container_spec.md](cron_container_spec.md#9-api--health-endpoints) — the API
  process cannot see the scheduler process's in-memory `ScheduleFile` directly, so this
  should either read `schedule.yaml` directly (source of truth, no extra IPC needed) or
  be added to the existing heartbeat file payload).
- All list/detail queries are parameterized SQL via SQLAlchemy `select()` — no string
  interpolation, consistent with Section 8 of the coding standard.
- No new environment variables or write endpoints are introduced by this spec.

## 6. Out of Scope

- Triggering, cancelling, or retrying a run via the API (read-only surface only).
- Authentication/authorization (assumed to sit behind existing network/gateway controls;
  add before exposing this API outside a trusted network).
- Real-time streaming of in-progress `stdout`/`stderr` (only the final captured output
  is available, once the run completes).

"""File-based heartbeat so the API process (separate from the scheduler process
under supervisord) can observe scheduler liveness without shared memory or a DB round trip.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime

from cron_runner.config import settings


def write_heartbeat(schedule_loaded: bool, job_count: int, last_reload_error: str | None) -> None:
    payload = {
        "heartbeat_at": datetime.now(UTC).isoformat(),
        "schedule_loaded": schedule_loaded,
        "job_count": job_count,
        "last_reload_error": last_reload_error,
    }
    directory = os.path.dirname(settings.heartbeat_file) or "."
    # write atomically so a concurrent reader never sees a partially written file
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".heartbeat-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp_path, settings.heartbeat_file)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def read_heartbeat() -> dict | None:
    try:
        with open(settings.heartbeat_file, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def heartbeat_age_seconds(payload: dict | None) -> float | None:
    if not payload or "heartbeat_at" not in payload:
        return None
    heartbeat_at = datetime.fromisoformat(payload["heartbeat_at"])
    return (datetime.now(UTC) - heartbeat_at).total_seconds()

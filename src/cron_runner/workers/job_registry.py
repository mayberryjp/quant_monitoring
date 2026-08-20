"""Thread-safe in-memory registry of in-flight jobs.

Used purely for overlap prevention and /ready reporting. It is NOT the durable
record of what ran -- that is the cron_runner_job_runs database table.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime


@dataclass
class RunningJob:
    execution_id: str
    started_at: datetime


class JobRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: dict[str, RunningJob] = {}

    def is_running(self, job_name: str) -> bool:
        with self._lock:
            return job_name in self._running

    def mark_started(self, job_name: str, execution_id: str, started_at: datetime) -> None:
        with self._lock:
            self._running[job_name] = RunningJob(execution_id=execution_id, started_at=started_at)

    def mark_finished(self, job_name: str) -> None:
        with self._lock:
            self._running.pop(job_name, None)

    def snapshot(self) -> dict[str, RunningJob]:
        with self._lock:
            return dict(self._running)

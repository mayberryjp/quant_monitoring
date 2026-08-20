"""SQLAlchemy table definitions for cron_runner's owned schema objects.

All objects are prefixed `cron_runner_` because the database may be shared with
other, unrelated projects/services (see docs/specs/cron_container_spec.md section 8.2).
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text

metadata = MetaData()

job_runs = Table(
    "cron_runner_job_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("job_name", String, nullable=False, index=True),
    Column("attempt", Integer, nullable=False, default=1),
    Column("status", String, nullable=False),
    Column("exit_code", Integer, nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("duration_ms", Integer, nullable=True),
    Column("stdout", Text, nullable=True),
    Column("stderr", Text, nullable=True),
    Column("error_message", Text, nullable=True),
)

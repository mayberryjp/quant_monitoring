"""create cron_runner_job_runs

Revision ID: 0001_create_cron_runner_job_runs
Revises:
Create Date: 2026-08-20

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_create_cron_runner_job_runs"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cron_runner_job_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("job_name", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("stdout", sa.Text(), nullable=True),
        sa.Column("stderr", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_cron_runner_job_runs_job_name",
        "cron_runner_job_runs",
        ["job_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_cron_runner_job_runs_job_name", table_name="cron_runner_job_runs")
    op.drop_table("cron_runner_job_runs")

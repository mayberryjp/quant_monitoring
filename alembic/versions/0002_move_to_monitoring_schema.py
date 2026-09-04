"""move cron_runner objects into the monitoring schema

Relocates cron_runner_job_runs from whichever default schema it was originally
created in into the dedicated `monitoring` schema, so every object owned by this
service is cataloged there. (The Alembic version table is relocated in
alembic/env.py, before this migration runs, since Alembic must read it first.)

Uses ALTER TABLE ... SET SCHEMA so existing rows persist; the table's indexes and
constraints move with it automatically.

Revision ID: 0002_move_to_monitoring_schema
Revises: 0001_create_cron_runner_job_runs
Create Date: 2026-09-04

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_move_to_monitoring_schema"
down_revision: str | None = "0001_create_cron_runner_job_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "monitoring"
TABLE = "cron_runner_job_runs"


def upgrade() -> None:
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
    # Find the table wherever it currently lives (any non-monitoring schema) and
    # move it. Guarded so this is a no-op once it already lives in `monitoring`.
    op.execute(
        f"""
        DO $$
        DECLARE
            src text;
        BEGIN
            SELECT table_schema INTO src
            FROM information_schema.tables
            WHERE table_name = '{TABLE}'
              AND table_type = 'BASE TABLE'
              AND table_schema NOT IN ('{SCHEMA}', 'pg_catalog', 'information_schema')
            ORDER BY table_schema
            LIMIT 1;

            IF src IS NOT NULL THEN
                EXECUTE format('ALTER TABLE %I.{TABLE} SET SCHEMA {SCHEMA}', src);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Move the table back into the connection's default schema.
    op.execute(
        f"""
        DO $$
        DECLARE
            dest text := current_schema();
        BEGIN
            IF dest IS DISTINCT FROM '{SCHEMA}'
               AND EXISTS (
                   SELECT 1 FROM information_schema.tables
                   WHERE table_schema = '{SCHEMA}' AND table_name = '{TABLE}'
               ) THEN
                EXECUTE format('ALTER TABLE "{SCHEMA}".{TABLE} SET SCHEMA %I', dest);
            END IF;
        END $$;
        """
    )

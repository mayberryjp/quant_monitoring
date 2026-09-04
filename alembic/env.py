"""Alembic environment. Reads DATABASE_URL from the environment and uses a
service-specific version table (alembic_version_cron_runner) because this
database may be shared with other, unrelated projects."""
from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context
from cron_runner.repository.models import SCHEMA_NAME, metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata

VERSION_TABLE = "alembic_version_cron_runner"


def get_url() -> str:
    url = os.environ.get("DATABASE_URL", config.get_main_option("sqlalchemy.url") or "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set (and alembic.ini has no sqlalchemy.url). "
            "Set DATABASE_URL in the environment before running 'alembic upgrade head'."
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
        version_table_schema=SCHEMA_NAME,
    )
    with context.begin_transaction():
        context.run_migrations()


def _prepare_schema(connection) -> None:
    """Ensure the `monitoring` schema exists and relocate a pre-existing Alembic
    version table into it, so Alembic finds its migration history under the new
    schema. Idempotent: a no-op once the version table already lives there."""
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA_NAME}"'))
    connection.execute(
        text(
            f"""
            DO $$
            DECLARE
                src text;
            BEGIN
                SELECT table_schema INTO src
                FROM information_schema.tables
                WHERE table_name = '{VERSION_TABLE}'
                  AND table_type = 'BASE TABLE'
                  AND table_schema NOT IN ('{SCHEMA_NAME}', 'pg_catalog', 'information_schema')
                ORDER BY table_schema
                LIMIT 1;

                IF src IS NOT NULL THEN
                    EXECUTE format(
                        'ALTER TABLE %I.{VERSION_TABLE} SET SCHEMA {SCHEMA_NAME}', src
                    );
                END IF;
            END $$;
            """
        )
    )


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        with connection.begin():
            _prepare_schema(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=VERSION_TABLE,
            version_table_schema=SCHEMA_NAME,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

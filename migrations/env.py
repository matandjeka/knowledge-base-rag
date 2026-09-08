"""Alembic environment for the production metadata database."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.schema import CreateSchema

from app.core.config import get_settings
from app.persistence.metadata import metadata

configuration = context.config
if configuration.config_file_name is not None:
    fileConfig(configuration.config_file_name)
settings = get_settings()
if settings.metadata_database_url is None:
    raise RuntimeError("METADATA_DATABASE_URL is required for Alembic")
configuration.set_main_option("sqlalchemy.url", settings.metadata_database_url.get_secret_value())
target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=configuration.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=settings.metadata_database_schema,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: object) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=settings.metadata_database_schema,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = async_engine_from_config(
        configuration.get_section(configuration.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"server_settings": {"search_path": settings.metadata_database_schema}},
    )
    async with engine.begin() as connection:
        await connection.execute(
            CreateSchema(settings.metadata_database_schema, if_not_exists=True)
        )
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())

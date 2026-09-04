"""Async SQLAlchemy boundary with read-only execution controls."""

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, Protocol

from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.core.exceptions import DatabaseConfigurationError, DatabaseExecutionError
from app.models import DatabaseDialect


class SecretResolver(Protocol):
    """Resolve a connection URL without persisting it in source metadata."""

    def resolve(self, reference: str) -> str:
        """Return a secret connection URL."""
        ...


class EnvironmentSecretResolver:
    """Resolve database URLs from explicitly named environment variables."""

    def resolve(self, reference: str) -> str:
        value = os.environ.get(reference)
        if not value:
            raise DatabaseConfigurationError(
                f"Database secret environment variable {reference!r} is not configured"
            )
        return value


class DatabaseConnectionManager:
    """Create controlled short-lived engines for external database sources."""

    def __init__(self, resolver: SecretResolver, *, timeout_seconds: float) -> None:
        self._resolver = resolver
        self._timeout_seconds = timeout_seconds
        self._engines: dict[tuple[str, DatabaseDialect], AsyncEngine] = {}
        self._engine_lock = asyncio.Lock()

    @asynccontextmanager
    async def connect(
        self,
        secret_reference: str,
        dialect: DatabaseDialect,
        *,
        allow_sqlite: bool = False,
    ) -> AsyncIterator[AsyncConnection]:
        """Open a read-only transaction after verifying the configured dialect."""
        try:
            engine = await self._get_engine(secret_reference, dialect, allow_sqlite=allow_sqlite)
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    if dialect is DatabaseDialect.POSTGRESQL:
                        await connection.execute(text("SET TRANSACTION READ ONLY"))
                        timeout_ms = max(1, int(self._timeout_seconds * 1000))
                        await connection.execute(
                            text("SELECT set_config('statement_timeout', :timeout, true)"),
                            {"timeout": f"{timeout_ms}ms"},
                        )
                    yield connection
                finally:
                    if transaction.is_active:
                        await transaction.rollback()
        except (SQLAlchemyError, OSError) as error:
            raise DatabaseExecutionError("Database operation failed") from error

    async def close(self) -> None:
        """Dispose all cached connection pools during application shutdown."""
        async with self._engine_lock:
            engines = list(self._engines.values())
            self._engines.clear()
        for engine in engines:
            await engine.dispose()

    async def execute(
        self,
        secret_reference: str,
        dialect: DatabaseDialect,
        sql: str,
        parameters: Mapping[str, Any],
        *,
        allow_sqlite: bool = False,
    ) -> list[dict[str, Any]]:
        """Execute one validated statement and return detached result mappings."""
        async with self.connect(secret_reference, dialect, allow_sqlite=allow_sqlite) as connection:
            result = await connection.execute(text(sql), dict(parameters))
            return [dict(row) for row in result.mappings().all()]

    async def _get_engine(
        self,
        secret_reference: str,
        dialect: DatabaseDialect,
        *,
        allow_sqlite: bool,
    ) -> AsyncEngine:
        if dialect is DatabaseDialect.SQLITE and not allow_sqlite:
            raise DatabaseConfigurationError("SQLite is available only through the test adapter")
        key = (secret_reference, dialect)
        async with self._engine_lock:
            cached = self._engines.get(key)
            if cached is not None:
                return cached
            try:
                url = self._resolver.resolve(secret_reference)
                parsed = make_url(url)
                backend = parsed.get_backend_name()
                expected = dialect.value
                if expected == "postgresql" and backend not in {"postgresql", "postgres"}:
                    raise DatabaseConfigurationError(
                        "Database URL does not match PostgreSQL dialect"
                    )
                if expected == "sqlite" and (backend != "sqlite" or not allow_sqlite):
                    raise DatabaseConfigurationError(
                        "SQLite is available only through the test adapter"
                    )
                if backend in {"postgresql", "postgres"} and "+asyncpg" not in parsed.drivername:
                    parsed = parsed.set(drivername="postgresql+asyncpg")
                elif backend == "sqlite" and "+aiosqlite" not in parsed.drivername:
                    parsed = parsed.set(drivername="sqlite+aiosqlite")
                engine = create_async_engine(parsed, pool_pre_ping=True)
            except DatabaseConfigurationError:
                raise
            except (SQLAlchemyError, OSError, ValueError) as error:
                raise DatabaseConfigurationError("Database connection URL is invalid") from error
            if backend == "sqlite":
                event.listen(engine.sync_engine, "connect", _enable_sqlite_query_only)
            self._engines[key] = engine
            return engine


def _enable_sqlite_query_only(dbapi_connection: Any, _: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA query_only = ON")
    cursor.close()

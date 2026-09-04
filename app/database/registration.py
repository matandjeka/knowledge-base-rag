"""Database source registration orchestration."""

from app.database.connection import DatabaseConnectionManager
from app.database.schema import inspect_allowed_tables
from app.models import (
    DatabaseDialect,
    DatabaseIsolationMode,
    DatabaseSourceRequest,
    DatabaseSourceResult,
    DatabaseTablePolicy,
    DatabaseTableSchema,
    Source,
    SourceConfig,
    SourceStatus,
    SourceType,
)
from app.repositories import SourceRepository


class DatabaseRegistrationService:
    """Verify and register sanitized workspace database configurations."""

    def __init__(
        self,
        repository: SourceRepository,
        connections: DatabaseConnectionManager,
        *,
        allow_sqlite: bool = False,
    ) -> None:
        self._repository = repository
        self._connections = connections
        self._allow_sqlite = allow_sqlite

    async def register(self, request: DatabaseSourceRequest) -> DatabaseSourceResult:
        """Validate connectivity, allowlist metadata, and isolation before registration."""
        if request.dialect is DatabaseDialect.SQLITE and not self._allow_sqlite:
            raise ValueError("SQLite is available only through the test adapter")
        async with self._connections.connect(
            request.secret_env_var,
            request.dialect,
            allow_sqlite=self._allow_sqlite,
        ) as connection:
            schemas = await inspect_allowed_tables(connection, request.tables)
        source = Source(
            workspace_id=request.workspace_id,
            name=request.name,
            config=SourceConfig(
                source_type=SourceType.DATABASE,
                uri=f"{request.dialect.value}://{request.name}",
                options={
                    "secret_env_var": request.secret_env_var,
                    "dialect": request.dialect.value,
                    "isolation_mode": request.isolation_mode.value,
                    "tables": [table.model_dump() for table in request.tables],
                    "schema": [schema.model_dump() for schema in schemas],
                },
            ),
        )
        await self._repository.create(source)
        await self._repository.transition(
            request.workspace_id, source.source_id, SourceStatus.INDEXING
        )
        source = await self._repository.transition(
            request.workspace_id, source.source_id, SourceStatus.READY
        )
        return DatabaseSourceResult(source=source, tables=schemas)


def source_database_config(
    source: Source,
) -> tuple[
    str,
    DatabaseDialect,
    DatabaseIsolationMode,
    list[DatabaseTablePolicy],
    list[DatabaseTableSchema],
]:
    """Reconstruct validated runtime policy from sanitized source metadata."""
    options = source.config.options
    return (
        str(options["secret_env_var"]),
        DatabaseDialect(str(options["dialect"])),
        DatabaseIsolationMode(str(options["isolation_mode"])),
        [DatabaseTablePolicy.model_validate(item) for item in options["tables"]],
        [DatabaseTableSchema.model_validate(item) for item in options["schema"]],
    )

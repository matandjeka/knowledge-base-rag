"""Authoritative source metadata and generation activation repositories."""

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.exceptions import (
    InvalidSourceTransitionError,
    SourceAlreadyExistsError,
    SourceNotFoundError,
)
from app.models import Source, SourceStatus, SourceUpdate


class GenerationKind(StrEnum):
    """Independently published retrieval generation families."""

    RETRIEVAL = "retrieval"
    GRAPH = "graph"


class OperationStatus(StrEnum):
    """Recoverable multi-store operation states."""

    PREPARING = "preparing"
    READY = "ready"
    COMPLETE = "complete"
    FAILED = "failed"


class PersistenceOperation(BaseModel):
    """One durable, retryable persistence operation."""

    model_config = ConfigDict(extra="forbid")

    operation_id: UUID = Field(default_factory=uuid4)
    workspace_id: str
    generation_id: UUID
    generation_kind: GenerationKind
    status: OperationStatus = OperationStatus.PREPARING
    prepared_stores: list[str] = Field(default_factory=list)
    error_category: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ActiveGenerationRepository(Protocol):
    async def active_generation(self, workspace_id: str, kind: GenerationKind) -> UUID: ...


class PersistenceRepository(ActiveGenerationRepository, Protocol):
    async def begin_operation(
        self, workspace_id: str, generation_id: UUID, kind: GenerationKind
    ) -> PersistenceOperation: ...

    async def mark_prepared(self, operation_id: UUID, store: str) -> PersistenceOperation: ...

    async def publish(self, operation_id: UUID, required_stores: frozenset[str]) -> None: ...

    async def fail(self, operation_id: UUID, error_category: str) -> None: ...

    async def operation_for_generation(
        self, workspace_id: str, generation_id: UUID, kind: GenerationKind
    ) -> PersistenceOperation: ...

    async def checkpoint_migration(
        self, migration_id: UUID, item_key: str, checksum: str
    ) -> None: ...

    async def migration_checkpoint(self, migration_id: UUID, item_key: str) -> str | None: ...


class InMemoryPersistenceRepository:
    """Concurrency-safe coordinator used by local development and tests."""

    def __init__(self) -> None:
        self._operations: dict[UUID, PersistenceOperation] = {}
        self._active: dict[tuple[str, GenerationKind], UUID] = {}
        self._checkpoints: dict[tuple[UUID, str], str] = {}
        self._lock = asyncio.Lock()

    async def begin_operation(
        self, workspace_id: str, generation_id: UUID, kind: GenerationKind
    ) -> PersistenceOperation:
        async with self._lock:
            operation = PersistenceOperation(
                workspace_id=workspace_id, generation_id=generation_id, generation_kind=kind
            )
            self._operations[operation.operation_id] = operation
            return operation.model_copy(deep=True)

    async def mark_prepared(self, operation_id: UUID, store: str) -> PersistenceOperation:
        async with self._lock:
            operation = self._operations[operation_id]
            stores = sorted({*operation.prepared_stores, store})
            operation = operation.model_copy(
                update={
                    "prepared_stores": stores,
                    "status": OperationStatus.READY,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._operations[operation_id] = operation
            return operation.model_copy(deep=True)

    async def publish(self, operation_id: UUID, required_stores: frozenset[str]) -> None:
        async with self._lock:
            operation = self._operations[operation_id]
            missing = required_stores - set(operation.prepared_stores)
            if missing:
                raise ValueError(
                    "Cannot publish before stores are prepared: " + ", ".join(sorted(missing))
                )
            self._active[(operation.workspace_id, operation.generation_kind)] = (
                operation.generation_id
            )
            self._operations[operation_id] = operation.model_copy(
                update={"status": OperationStatus.COMPLETE, "updated_at": datetime.now(UTC)}
            )

    async def fail(self, operation_id: UUID, error_category: str) -> None:
        async with self._lock:
            operation = self._operations[operation_id]
            self._operations[operation_id] = operation.model_copy(
                update={
                    "status": OperationStatus.FAILED,
                    "error_category": error_category,
                    "updated_at": datetime.now(UTC),
                }
            )

    async def operation_for_generation(
        self, workspace_id: str, generation_id: UUID, kind: GenerationKind
    ) -> PersistenceOperation:
        async with self._lock:
            matches = [
                operation
                for operation in self._operations.values()
                if operation.workspace_id == workspace_id
                and operation.generation_id == generation_id
                and operation.generation_kind is kind
            ]
            if not matches:
                raise ValueError("Persistence operation was not found")
            return max(matches, key=lambda operation: operation.created_at).model_copy(deep=True)

    async def active_generation(self, workspace_id: str, kind: GenerationKind) -> UUID:
        from app.core.exceptions import IndexNotFoundError

        async with self._lock:
            generation = self._active.get((workspace_id, kind))
            if generation is None:
                raise IndexNotFoundError(f"No active {kind.value} generation exists")
            return generation

    async def checkpoint_migration(self, migration_id: UUID, item_key: str, checksum: str) -> None:
        async with self._lock:
            existing = self._checkpoints.get((migration_id, item_key))
            if existing is not None and existing != checksum:
                raise ValueError("Migration checkpoint checksum changed")
            self._checkpoints[(migration_id, item_key)] = checksum

    async def migration_checkpoint(self, migration_id: UUID, item_key: str) -> str | None:
        async with self._lock:
            return self._checkpoints.get((migration_id, item_key))


metadata = MetaData()
sources_table = Table(
    "rag_sources",
    metadata,
    Column("source_id", String(36), primary_key=True),
    Column("workspace_id", String(64), nullable=False, index=True),
    Column("name", String(500), nullable=False),
    Column("config", JSON, nullable=False),
    Column("status", String(32), nullable=False),
    Column("classification", String(16), nullable=False, server_default="internal"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False, default=1),
    UniqueConstraint("workspace_id", "source_id"),
)
active_generations_table = Table(
    "rag_active_generations",
    metadata,
    Column("workspace_id", String(64), primary_key=True),
    Column("kind", String(32), primary_key=True),
    Column("generation_id", String(36), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
operations_table = Table(
    "rag_persistence_operations",
    metadata,
    Column("operation_id", String(36), primary_key=True),
    Column("workspace_id", String(64), nullable=False, index=True),
    Column("generation_id", String(36), nullable=False),
    Column("generation_kind", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("prepared_stores", JSON, nullable=False),
    Column("error_category", String(128)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
migration_checkpoints_table = Table(
    "rag_migration_checkpoints",
    metadata,
    Column("migration_id", String(36), primary_key=True),
    Column("item_key", String(500), primary_key=True),
    Column("checksum", String(64), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=False),
)


async def create_metadata_schema(engine: AsyncEngine) -> None:
    """Test/dev helper; production schema changes are owned by Alembic."""
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)


class PostgresPersistenceRepository:
    """Transactional SQLAlchemy implementation of source and generation metadata."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)
        self._dialect_name = engine.dialect.name

    async def create(self, source: Source) -> Source:
        if source.status is not SourceStatus.REGISTERED:
            raise InvalidSourceTransitionError("New source must be registered")
        try:
            async with self._sessions.begin() as session:
                await session.execute(
                    insert(sources_table).values(**_source_values(source), version=1)
                )
        except IntegrityError as error:
            raise SourceAlreadyExistsError(f"Source {source.source_id} already exists") from error
        return source.model_copy(deep=True)

    async def get(self, workspace_id: str, source_id: UUID) -> Source:
        async with self._sessions() as session:
            row = (
                (await session.execute(_source_select(workspace_id, source_id)))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise SourceNotFoundError(f"Source {source_id} was not found")
        return _source_from_row(dict(row))

    async def list(self, workspace_id: str) -> Sequence[Source]:
        statement = (
            select(sources_table)
            .where(sources_table.c.workspace_id == workspace_id)
            .order_by(sources_table.c.created_at, sources_table.c.source_id)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).mappings().all()
        return tuple(_source_from_row(dict(row)) for row in rows)

    async def update(self, workspace_id: str, source_id: UUID, changes: SourceUpdate) -> Source:
        async with self._sessions.begin() as session:
            current = await self._locked_source(session, workspace_id, source_id)
            values: dict[str, Any] = {
                "updated_at": datetime.now(UTC),
                "version": current["version"] + 1,
            }
            if "name" in changes.model_fields_set:
                values["name"] = changes.name
            if "config" in changes.model_fields_set and changes.config is not None:
                values["config"] = changes.config.model_dump(mode="json")
            if "classification" in changes.model_fields_set and changes.classification is not None:
                values["classification"] = changes.classification.value
            await session.execute(
                _source_select(workspace_id, source_id).with_only_columns(sources_table.c.source_id)
            )
            await session.execute(
                update(sources_table)
                .where(
                    sources_table.c.workspace_id == workspace_id,
                    sources_table.c.source_id == str(source_id),
                )
                .values(**values)
            )
        return await self.get(workspace_id, source_id)

    async def transition(
        self,
        workspace_id: str,
        source_id: UUID,
        status: SourceStatus,
        *,
        transitioned_at: datetime | None = None,
    ) -> Source:
        async with self._sessions.begin() as session:
            current = await self._locked_source(session, workspace_id, source_id)
            if status not in _allowed_transitions(SourceStatus(current["status"])):
                raise InvalidSourceTransitionError(
                    f"Cannot transition source from {current['status']} to {status}"
                )
            await session.execute(
                update(sources_table)
                .where(
                    sources_table.c.workspace_id == workspace_id,
                    sources_table.c.source_id == str(source_id),
                )
                .values(
                    status=status.value,
                    updated_at=transitioned_at or datetime.now(UTC),
                    version=current["version"] + 1,
                )
            )
        return await self.get(workspace_id, source_id)

    async def delete(self, workspace_id: str, source_id: UUID) -> None:
        async with self._sessions.begin() as session:
            result = await session.execute(
                delete(sources_table).where(
                    sources_table.c.workspace_id == workspace_id,
                    sources_table.c.source_id == str(source_id),
                )
            )
            if getattr(result, "rowcount", 0) != 1:
                raise SourceNotFoundError(f"Source {source_id} was not found")

    async def begin_operation(
        self, workspace_id: str, generation_id: UUID, kind: GenerationKind
    ) -> PersistenceOperation:
        operation = PersistenceOperation(
            workspace_id=workspace_id, generation_id=generation_id, generation_kind=kind
        )
        async with self._sessions.begin() as session:
            await session.execute(insert(operations_table).values(**_operation_values(operation)))
        return operation

    async def mark_prepared(self, operation_id: UUID, store: str) -> PersistenceOperation:
        async with self._sessions.begin() as session:
            row = await self._locked_operation(session, operation_id)
            stores = sorted({*row["prepared_stores"], store})
            await session.execute(
                update(operations_table)
                .where(operations_table.c.operation_id == str(operation_id))
                .values(
                    prepared_stores=stores,
                    status=OperationStatus.READY.value,
                    updated_at=datetime.now(UTC),
                )
            )
        return await self._operation(operation_id)

    async def publish(self, operation_id: UUID, required_stores: frozenset[str]) -> None:
        async with self._sessions.begin() as session:
            row = await self._locked_operation(session, operation_id)
            missing = required_stores - set(row["prepared_stores"])
            if missing:
                raise ValueError(
                    "Cannot publish before stores are prepared: " + ", ".join(sorted(missing))
                )
            values = {
                "workspace_id": row["workspace_id"],
                "kind": row["generation_kind"],
                "generation_id": row["generation_id"],
                "updated_at": datetime.now(UTC),
            }
            if self._dialect_name == "postgresql":
                statement = postgres_insert(active_generations_table).values(**values)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["workspace_id", "kind"],
                        set_={
                            "generation_id": statement.excluded.generation_id,
                            "updated_at": statement.excluded.updated_at,
                        },
                    )
                )
            else:
                existing = (
                    (
                        await session.execute(
                            select(active_generations_table)
                            .where(
                                active_generations_table.c.workspace_id == row["workspace_id"],
                                active_generations_table.c.kind == row["generation_kind"],
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is None:
                    await session.execute(insert(active_generations_table).values(**values))
                else:
                    await session.execute(
                        update(active_generations_table)
                        .where(
                            active_generations_table.c.workspace_id == row["workspace_id"],
                            active_generations_table.c.kind == row["generation_kind"],
                        )
                        .values(**values)
                    )
            await session.execute(
                update(operations_table)
                .where(operations_table.c.operation_id == str(operation_id))
                .values(status=OperationStatus.COMPLETE.value, updated_at=datetime.now(UTC))
            )

    async def fail(self, operation_id: UUID, error_category: str) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(operations_table)
                .where(operations_table.c.operation_id == str(operation_id))
                .values(
                    status=OperationStatus.FAILED.value,
                    error_category=error_category,
                    updated_at=datetime.now(UTC),
                )
            )

    async def operation_for_generation(
        self, workspace_id: str, generation_id: UUID, kind: GenerationKind
    ) -> PersistenceOperation:
        statement = (
            select(operations_table)
            .where(
                operations_table.c.workspace_id == workspace_id,
                operations_table.c.generation_id == str(generation_id),
                operations_table.c.generation_kind == kind.value,
            )
            .order_by(operations_table.c.created_at.desc())
            .limit(1)
        )
        async with self._sessions() as session:
            row = (await session.execute(statement)).mappings().one_or_none()
        if row is None:
            raise ValueError("Persistence operation was not found")
        return PersistenceOperation.model_validate(dict(row))

    async def active_generation(self, workspace_id: str, kind: GenerationKind) -> UUID:
        from app.core.exceptions import IndexNotFoundError

        async with self._sessions() as session:
            value = await session.scalar(
                select(active_generations_table.c.generation_id).where(
                    active_generations_table.c.workspace_id == workspace_id,
                    active_generations_table.c.kind == kind.value,
                )
            )
        if value is None:
            raise IndexNotFoundError(f"No active {kind.value} generation exists")
        return UUID(value)

    async def checkpoint_migration(self, migration_id: UUID, item_key: str, checksum: str) -> None:
        async with self._sessions.begin() as session:
            existing = await session.scalar(
                select(migration_checkpoints_table.c.checksum).where(
                    migration_checkpoints_table.c.migration_id == str(migration_id),
                    migration_checkpoints_table.c.item_key == item_key,
                )
            )
            if existing is not None and existing != checksum:
                raise ValueError("Migration checkpoint checksum changed")
            if existing is None:
                await session.execute(
                    insert(migration_checkpoints_table).values(
                        migration_id=str(migration_id),
                        item_key=item_key,
                        checksum=checksum,
                        completed_at=datetime.now(UTC),
                    )
                )

    async def migration_checkpoint(self, migration_id: UUID, item_key: str) -> str | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(migration_checkpoints_table.c.checksum).where(
                    migration_checkpoints_table.c.migration_id == str(migration_id),
                    migration_checkpoints_table.c.item_key == item_key,
                )
            )

    async def _locked_source(
        self, session: AsyncSession, workspace_id: str, source_id: UUID
    ) -> Mapping[str, Any]:
        row = (
            (await session.execute(_source_select(workspace_id, source_id).with_for_update()))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise SourceNotFoundError(f"Source {source_id} was not found")
        return dict(row)

    async def _locked_operation(
        self, session: AsyncSession, operation_id: UUID
    ) -> Mapping[str, Any]:
        row = (
            (
                await session.execute(
                    select(operations_table)
                    .where(operations_table.c.operation_id == str(operation_id))
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        return dict(row)

    async def _operation(self, operation_id: UUID) -> PersistenceOperation:
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(operations_table).where(
                            operations_table.c.operation_id == str(operation_id)
                        )
                    )
                )
                .mappings()
                .one()
            )
        return PersistenceOperation.model_validate(dict(row))


def _source_select(workspace_id: str, source_id: UUID) -> Any:
    return select(sources_table).where(
        sources_table.c.workspace_id == workspace_id,
        sources_table.c.source_id == str(source_id),
    )


def _source_values(source: Source) -> dict[str, Any]:
    return {
        "source_id": str(source.source_id),
        "workspace_id": source.workspace_id,
        "name": source.name,
        "config": source.config.model_dump(mode="json"),
        "status": source.status.value,
        "classification": source.classification.value,
        "created_at": source.created_at,
        "updated_at": source.updated_at,
    }


def _source_from_row(row: Mapping[str, Any]) -> Source:
    return Source.model_validate(
        {
            "source_id": row["source_id"],
            "workspace_id": row["workspace_id"],
            "name": row["name"],
            "config": row["config"],
            "status": row["status"],
            "classification": row.get("classification", "internal"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


def _operation_values(operation: PersistenceOperation) -> dict[str, Any]:
    payload = operation.model_dump(mode="python")
    payload["operation_id"] = str(operation.operation_id)
    payload["generation_id"] = str(operation.generation_id)
    payload["generation_kind"] = operation.generation_kind.value
    payload["status"] = operation.status.value
    return payload


def _allowed_transitions(status: SourceStatus) -> frozenset[SourceStatus]:
    return {
        SourceStatus.REGISTERED: frozenset({SourceStatus.INDEXING, SourceStatus.FAILED}),
        SourceStatus.INDEXING: frozenset({SourceStatus.READY, SourceStatus.FAILED}),
        SourceStatus.READY: frozenset({SourceStatus.FAILED}),
        SourceStatus.FAILED: frozenset({SourceStatus.INDEXING}),
    }[status]

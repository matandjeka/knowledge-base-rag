"""Source registry contract and local in-memory implementation."""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.core.exceptions import (
    InvalidSourceTransitionError,
    SourceAlreadyExistsError,
    SourceNotFoundError,
)
from app.models import Source, SourceStatus, SourceUpdate

_ALLOWED_TRANSITIONS: dict[SourceStatus, frozenset[SourceStatus]] = {
    SourceStatus.REGISTERED: frozenset({SourceStatus.INDEXING, SourceStatus.FAILED}),
    SourceStatus.INDEXING: frozenset({SourceStatus.READY, SourceStatus.FAILED}),
    SourceStatus.READY: frozenset(),
    SourceStatus.FAILED: frozenset({SourceStatus.INDEXING}),
}


class SourceRepository(Protocol):
    """Async persistence boundary for workspace-scoped source metadata."""

    async def create(self, source: Source) -> Source:
        """Register a source and return the stored representation."""
        ...

    async def get(self, workspace_id: str, source_id: UUID) -> Source:
        """Return one source within a workspace."""
        ...

    async def list(self, workspace_id: str) -> Sequence[Source]:
        """Return sources belonging to a workspace in creation order."""
        ...

    async def update(self, workspace_id: str, source_id: UUID, changes: SourceUpdate) -> Source:
        """Update mutable metadata without changing lifecycle state."""
        ...

    async def transition(self, workspace_id: str, source_id: UUID, status: SourceStatus) -> Source:
        """Apply a valid lifecycle transition."""
        ...

    async def delete(self, workspace_id: str, source_id: UUID) -> None:
        """Remove source metadata within a workspace."""
        ...


class InMemorySourceRepository:
    """Concurrency-safe, process-local source registry for development and tests."""

    def __init__(self) -> None:
        self._sources: dict[UUID, Source] = {}
        self._lock = asyncio.Lock()

    async def create(self, source: Source) -> Source:
        """Register a new source in its required initial lifecycle state."""
        async with self._lock:
            if source.source_id in self._sources:
                raise SourceAlreadyExistsError(f"Source {source.source_id} already exists")
            if source.status is not SourceStatus.REGISTERED:
                raise InvalidSourceTransitionError(
                    f"New source must be {SourceStatus.REGISTERED}; received {source.status}"
                )
            stored = source.model_copy(deep=True)
            self._sources[source.source_id] = stored
            return stored.model_copy(deep=True)

    async def get(self, workspace_id: str, source_id: UUID) -> Source:
        """Return a defensive copy without disclosing cross-workspace sources."""
        async with self._lock:
            return self._get_scoped(workspace_id, source_id).model_copy(deep=True)

    async def list(self, workspace_id: str) -> Sequence[Source]:
        """Return defensive copies in deterministic creation order."""
        async with self._lock:
            sources = (
                source for source in self._sources.values() if source.workspace_id == workspace_id
            )
            ordered = sorted(sources, key=lambda source: (source.created_at, str(source.source_id)))
            return tuple(source.model_copy(deep=True) for source in ordered)

    async def update(self, workspace_id: str, source_id: UUID, changes: SourceUpdate) -> Source:
        """Update allowed metadata fields and refresh the modification time."""
        async with self._lock:
            current = self._get_scoped(workspace_id, source_id)
            values = {field: getattr(changes, field) for field in changes.model_fields_set}
            updated = current.model_copy(
                update={**values, "updated_at": datetime.now(UTC)}, deep=True
            )
            self._sources[source_id] = updated
            return updated.model_copy(deep=True)

    async def transition(self, workspace_id: str, source_id: UUID, status: SourceStatus) -> Source:
        """Apply a permitted state change and refresh the modification time."""
        async with self._lock:
            current = self._get_scoped(workspace_id, source_id)
            if status not in _ALLOWED_TRANSITIONS[current.status]:
                raise InvalidSourceTransitionError(
                    f"Cannot transition source from {current.status} to {status}"
                )
            updated = current.model_copy(
                update={"status": status, "updated_at": datetime.now(UTC)}, deep=True
            )
            self._sources[source_id] = updated
            return updated.model_copy(deep=True)

    async def delete(self, workspace_id: str, source_id: UUID) -> None:
        """Delete a workspace-scoped source."""
        async with self._lock:
            self._get_scoped(workspace_id, source_id)
            del self._sources[source_id]

    def _get_scoped(self, workspace_id: str, source_id: UUID) -> Source:
        source = self._sources.get(source_id)
        if source is None or source.workspace_id != workspace_id:
            raise SourceNotFoundError(f"Source {source_id} was not found")
        return source

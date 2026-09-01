"""Source registry contract tests."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.core.exceptions import (
    InvalidSourceTransitionError,
    SourceAlreadyExistsError,
    SourceNotFoundError,
)
from app.models import Source, SourceConfig, SourceStatus, SourceType, SourceUpdate
from app.repositories import InMemorySourceRepository, SourceRepository


def _source(*, workspace_id: str = "workspace-a", name: str = "Handbook") -> Source:
    return Source(
        workspace_id=workspace_id,
        name=name,
        config=SourceConfig(source_type=SourceType.PDF),
    )


def _accepts_repository(_: SourceRepository) -> None:
    """Statically assert that the adapter satisfies the repository protocol."""


@pytest.fixture
def repository() -> InMemorySourceRepository:
    registry = InMemorySourceRepository()
    _accepts_repository(registry)
    return registry


@pytest.mark.asyncio
async def test_create_get_and_list_are_workspace_scoped(
    repository: InMemorySourceRepository,
) -> None:
    first = _source()
    second = _source(workspace_id="workspace-b", name="Policies")

    await repository.create(first)
    await repository.create(second)

    assert await repository.get("workspace-a", first.source_id) == first
    assert [source.source_id for source in await repository.list("workspace-a")] == [
        first.source_id
    ]
    with pytest.raises(SourceNotFoundError):
        await repository.get("workspace-b", first.source_id)


@pytest.mark.asyncio
async def test_create_rejects_duplicate_source_ids(
    repository: InMemorySourceRepository,
) -> None:
    source = _source()
    await repository.create(source)

    with pytest.raises(SourceAlreadyExistsError):
        await repository.create(source)


@pytest.mark.asyncio
async def test_create_requires_registered_initial_status(
    repository: InMemorySourceRepository,
) -> None:
    source = _source().model_copy(update={"status": SourceStatus.READY})

    with pytest.raises(InvalidSourceTransitionError):
        await repository.create(source)


@pytest.mark.asyncio
async def test_results_are_defensive_copies(repository: InMemorySourceRepository) -> None:
    source = _source()
    created = await repository.create(source)
    created.name = "Changed outside repository"

    stored = await repository.get(source.workspace_id, source.source_id)

    assert stored.name == "Handbook"


@pytest.mark.asyncio
async def test_update_changes_metadata_and_timestamp(
    repository: InMemorySourceRepository,
) -> None:
    source = _source()
    source.updated_at = datetime.now(UTC) - timedelta(seconds=1)
    await repository.create(source)

    updated = await repository.update(
        source.workspace_id,
        source.source_id,
        SourceUpdate(name="Employee Handbook"),
    )

    assert updated.name == "Employee Handbook"
    assert updated.updated_at > source.updated_at
    assert updated.status is SourceStatus.REGISTERED


@pytest.mark.asyncio
async def test_update_preserves_typed_source_config(
    repository: InMemorySourceRepository,
) -> None:
    source = _source()
    await repository.create(source)
    config = SourceConfig(source_type=SourceType.WEBSITE, uri="https://example.com")

    updated = await repository.update(
        source.workspace_id,
        source.source_id,
        SourceUpdate(config=config),
    )

    assert updated.config == config
    assert isinstance(updated.config, SourceConfig)


@pytest.mark.parametrize("field", ["name", "config"])
def test_source_update_rejects_explicit_nulls(field: str) -> None:
    with pytest.raises(ValidationError):
        SourceUpdate.model_validate({field: None})


@pytest.mark.asyncio
async def test_lifecycle_allows_processing_success_and_retry(
    repository: InMemorySourceRepository,
) -> None:
    source = _source()
    await repository.create(source)

    indexing = await repository.transition(
        source.workspace_id, source.source_id, SourceStatus.INDEXING
    )
    failed = await repository.transition(source.workspace_id, source.source_id, SourceStatus.FAILED)
    retried = await repository.transition(
        source.workspace_id, source.source_id, SourceStatus.INDEXING
    )
    ready = await repository.transition(source.workspace_id, source.source_id, SourceStatus.READY)

    assert [indexing.status, failed.status, retried.status, ready.status] == [
        SourceStatus.INDEXING,
        SourceStatus.FAILED,
        SourceStatus.INDEXING,
        SourceStatus.READY,
    ]


@pytest.mark.asyncio
async def test_lifecycle_rejects_invalid_transition(
    repository: InMemorySourceRepository,
) -> None:
    source = _source()
    await repository.create(source)

    with pytest.raises(InvalidSourceTransitionError):
        await repository.transition(source.workspace_id, source.source_id, SourceStatus.READY)


@pytest.mark.asyncio
async def test_delete_is_workspace_scoped(repository: InMemorySourceRepository) -> None:
    source = _source()
    await repository.create(source)

    with pytest.raises(SourceNotFoundError):
        await repository.delete("workspace-b", source.source_id)

    await repository.delete(source.workspace_id, source.source_id)
    with pytest.raises(SourceNotFoundError):
        await repository.get(source.workspace_id, source.source_id)

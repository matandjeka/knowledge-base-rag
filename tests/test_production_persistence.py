"""Phase 16 persistence authority and restart-safety tests."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.exceptions import IndexNotFoundError
from app.models import NormalizedDocument, Source, SourceConfig, SourceStatus, SourceType
from app.persistence import (
    GenerationKind,
    InMemoryPersistenceRepository,
    PostgresPersistenceRepository,
    create_metadata_schema,
)
from app.retrieval.blob_lexical import BlobLexicalStore
from app.storage.azure_blob import AzureBlobSourceStorage


class _Download:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def readall(self) -> bytes:
        return self._payload


class _Blob:
    def __init__(self, values: dict[str, bytes], name: str) -> None:
        self._values = values
        self._name = name

    async def upload_blob(self, payload: bytes, **options: Any) -> None:
        del options
        self._values[self._name] = payload

    async def download_blob(self) -> _Download:
        return _Download(self._values[self._name])


class _Container:
    container_name = "rag"

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def get_blob_client(self, name: str) -> _Blob:
        return _Blob(self.values, name)

    async def list_blobs(self, *, name_starts_with: str) -> Any:
        for name in self.values:
            if name.startswith(name_starts_with):
                yield SimpleNamespace(name=name)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_generation_is_invisible_until_every_required_store_is_prepared() -> None:
    repository = InMemoryPersistenceRepository()
    generation_id = uuid4()
    operation = await repository.begin_operation(
        "workspace", generation_id, GenerationKind.RETRIEVAL
    )

    await repository.mark_prepared(operation.operation_id, "vector")
    with pytest.raises(ValueError, match="lexical"):
        await repository.publish(operation.operation_id, frozenset({"vector", "lexical"}))
    with pytest.raises(IndexNotFoundError):
        await repository.active_generation("workspace", GenerationKind.RETRIEVAL)

    await repository.mark_prepared(operation.operation_id, "lexical")
    await repository.publish(operation.operation_id, frozenset({"vector", "lexical"}))

    assert (
        await repository.active_generation("workspace", GenerationKind.RETRIEVAL) == generation_id
    )


@pytest.mark.asyncio
async def test_migration_checkpoints_are_idempotent_and_detect_changed_content() -> None:
    repository = InMemoryPersistenceRepository()
    migration_id = uuid4()

    await repository.checkpoint_migration(migration_id, "source/one", "a" * 64)
    await repository.checkpoint_migration(migration_id, "source/one", "a" * 64)

    assert await repository.migration_checkpoint(migration_id, "source/one") == "a" * 64
    with pytest.raises(ValueError, match="checksum changed"):
        await repository.checkpoint_migration(migration_id, "source/one", "b" * 64)


@pytest.mark.asyncio
async def test_sql_metadata_and_active_generation_survive_repository_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "metadata.db"
    url = f"sqlite+aiosqlite:///{database}"
    first_engine = create_async_engine(url)
    await create_metadata_schema(first_engine)
    first = PostgresPersistenceRepository(first_engine)
    source = Source(
        workspace_id="workspace",
        name="persistent source",
        config=SourceConfig(source_type=SourceType.PDF),
    )
    await first.create(source)
    await first.transition("workspace", source.source_id, SourceStatus.INDEXING)
    await first.transition("workspace", source.source_id, SourceStatus.READY)
    generation_id = uuid4()
    operation = await first.begin_operation("workspace", generation_id, GenerationKind.RETRIEVAL)
    await first.mark_prepared(operation.operation_id, "vector")
    await first.mark_prepared(operation.operation_id, "lexical")
    await first.publish(operation.operation_id, frozenset({"vector", "lexical"}))
    await first_engine.dispose()

    second_engine = create_async_engine(url)
    second = PostgresPersistenceRepository(second_engine)
    try:
        restored = await second.get("workspace", source.source_id)
        active = await second.active_generation("workspace", GenerationKind.RETRIEVAL)
    finally:
        await second_engine.dispose()

    assert restored.status is SourceStatus.READY
    assert restored.source_id == source.source_id
    assert active == generation_id


@pytest.mark.asyncio
async def test_blob_source_documents_survive_adapter_restart() -> None:
    container = _Container()
    source_id = uuid4()
    document = NormalizedDocument(
        workspace_id="workspace",
        source_id=source_id,
        source_type=SourceType.PDF,
        content="Policy HR-402 requires annual review.",
        page_number=4,
    )
    first = AzureBlobSourceStorage(container)
    await first.save_documents("workspace", source_id, [document])

    second = AzureBlobSourceStorage(container)

    assert await second.load_documents("workspace", source_id) == (document,)


@pytest.mark.asyncio
async def test_blob_lexical_store_reloads_postgres_active_generation_after_restart() -> None:
    container = _Container()
    coordinator = InMemoryPersistenceRepository()
    generation_id = uuid4()
    document = NormalizedDocument(
        workspace_id="workspace",
        source_id=uuid4(),
        source_type=SourceType.PDF,
        content="Policy HR-402 requires annual review.",
        page_number=4,
    )
    operation = await coordinator.begin_operation(
        "workspace", generation_id, GenerationKind.RETRIEVAL
    )
    first = BlobLexicalStore(container, coordinator, max_cached_generations=1)
    await first.prepare("workspace", [document], generation_id=generation_id)
    await coordinator.mark_prepared(operation.operation_id, "lexical")
    await coordinator.publish(operation.operation_id, frozenset({"lexical"}))

    restarted = BlobLexicalStore(container, coordinator, max_cached_generations=1)
    matches = await restarted.search(
        "workspace",
        "HR-402",
        top_k=3,
        source_ids=frozenset(),
        min_score=0,
        title_boost=0.5,
    )

    assert [match.document.document_id for match in matches] == [document.document_id]

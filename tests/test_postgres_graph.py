"""Graph snapshot durability, publication, isolation, and integrity."""

from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.core.exceptions import GraphIndexNotFoundError, IndexingError
from app.graph.postgres_store import PostgresGraphStore
from app.persistence import GenerationKind, PostgresPersistenceRepository, create_metadata_schema
from app.persistence.metadata import graph_snapshots
from tests.test_graph_retrieval import _benchmark_snapshot


@pytest.mark.asyncio
async def test_postgres_graph_lifecycle(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'graph.db'}"
    engine = create_async_engine(url)
    await create_metadata_schema(engine)
    repository = PostgresPersistenceRepository(engine)
    store = PostgresGraphStore(engine, repository)
    snapshot, _, _ = _benchmark_snapshot()
    metadata = await store.prepare(snapshot)
    assert await store.prepare(snapshot) == metadata
    with pytest.raises(IndexingError, match="Immutable"):
        await store.prepare(snapshot.model_copy(update={"extractor_model": "changed"}))
    with pytest.raises(GraphIndexNotFoundError):
        await store.load(snapshot.workspace_id)
    with pytest.raises(GraphIndexNotFoundError):
        await store.activate("another-workspace", snapshot.generation_id)
    await store.activate(snapshot.workspace_id, snapshot.generation_id)
    with pytest.raises(GraphIndexNotFoundError):
        await store.load(snapshot.workspace_id)
    operation = await repository.begin_operation(
        snapshot.workspace_id, snapshot.generation_id, GenerationKind.GRAPH
    )
    await repository.mark_prepared(operation.operation_id, "graph")
    await repository.publish(operation.operation_id, frozenset({"graph"}))
    await engine.dispose()
    engine = create_async_engine(url)
    store = PostgresGraphStore(engine, PostgresPersistenceRepository(engine))
    assert await store.load(snapshot.workspace_id) == snapshot
    with pytest.raises(GraphIndexNotFoundError):
        await store.load("another-workspace")
    async with engine.begin() as connection:
        await connection.execute(
            update(graph_snapshots).values(
                snapshot_json=snapshot.model_copy(
                    update={"extractor_model": "tampered"}
                ).model_dump_json()
            )
        )
    with pytest.raises(IndexingError, match="integrity"):
        await store.load(snapshot.workspace_id)
    await engine.dispose()


def test_graph_postgres_requires_durable_metadata() -> None:
    with pytest.raises(ValueError, match="METADATA_STORE_BACKEND"):
        Settings(_env_file=None, graph_store_backend="postgresql")
    settings = Settings(
        _env_file=None,
        graph_store_backend="postgresql",
        metadata_store_backend="postgresql",
        metadata_database_url="postgresql+asyncpg://user:pass@localhost/db",
    )
    assert settings.neo4j_uri is None

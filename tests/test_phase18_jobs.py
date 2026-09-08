"""Durable job acceptance: idempotency, workspace isolation, and retry checkpoints."""

from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine

from app.jobs.processing import step_job
from app.jobs.repository import JobRepository
from app.persistence.metadata import metadata


@pytest.fixture
async def repository(tmp_path: Any) -> Any:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/jobs.db")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield JobRepository(engine)
    await engine.dispose()


@pytest.mark.asyncio
async def test_idempotency_and_workspace_serialization(repository: JobRepository) -> None:
    job_id = str(uuid4())
    job = await repository.create("one", {"kind": "pdf"}, job_id)
    assert await repository.create("one", {"kind": "pdf"}, job_id) == job
    with pytest.raises(HTTPException):
        await repository.create("two", {"kind": "pdf"}, job_id)
    with pytest.raises(HTTPException):
        await repository.create("one", {"kind": "csv"}, job_id)
    with pytest.raises(HTTPException):
        await repository.create("one", {"kind": "pdf"}, str(uuid4()))
    with pytest.raises(HTTPException):
        await repository.get("two", job_id)
    assert await repository.list("two") == []
    await repository.cancel("one", job_id)
    await repository.create("one", {"kind": "pdf"}, str(uuid4()))


@pytest.mark.asyncio
async def test_step_retries_rollback_and_duplicate_delivery_is_noop(
    repository: JobRepository, monkeypatch: Any
) -> None:
    import app.jobs.processing as processing

    monkeypatch.setattr(processing, "get_metadata_engine", lambda: repository.engine)
    calls = 0

    async def advance(job: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary provider error")
        return {**job["checkpoint"], "stage": "complete"}

    monkeypatch.setattr(processing, "advance", advance)
    job_id = str(uuid4())
    await repository.create("one", {"kind": "pdf"}, job_id)
    with pytest.raises(RuntimeError):
        await step_job(job_id, 0)
    assert (await repository.get("one", job_id))["step"] == 0
    restarted = JobRepository(repository.engine)
    assert (await restarted.get("one", job_id))["status"] == "queued"
    assert (await step_job(job_id, 0))["status"] == "complete"
    await step_job(job_id, 0)
    assert calls == 2
    await repository.create("one", {"kind": "pdf"}, str(uuid4()))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["csv", "pdf"])
async def test_csv_job_indexes_both_representations_and_publishes_after_retry(
    repository: JobRepository,
    monkeypatch: Any,
    kind: str,
) -> None:
    import numpy as np

    from app.api.dependencies import get_csv_ingestion_service
    from app.core.config import Settings
    from app.jobs import processing
    from app.models import SourceStatus
    from app.persistence import GenerationKind, InMemoryPersistenceRepository
    from app.repositories import InMemorySourceRepository
    from app.retrieval.pinecone_store import PineconeVectorStore
    from app.retrieval.vector_store import VectorGenerationMetadata
    from app.storage.vercel_blob import VercelBlobLexicalStore, VercelBlobSourceStorage
    from tests.test_phase18_hosted import MemoryBlob

    source_repository = InMemorySourceRepository()
    blobs = MemoryBlob()
    object_store = VercelBlobSourceStorage("fake", client=blobs)
    coordinator = InMemoryPersistenceRepository()
    lexical = VercelBlobLexicalStore(object_store, coordinator)

    class Embeddings:
        model_name = "voyage-test"
        dimension = 2

        async def embed_documents(self, texts: Any) -> Any:
            return np.asarray([[0.6, 0.8] for _ in texts], dtype=np.float32)

    class Vectors(PineconeVectorStore):
        def __init__(self) -> None:
            self.batches: list[str] = []
            self.finished = False

        async def upsert_job_batch(self, *args: Any, **kwargs: Any) -> None:
            self.batches.append(str(args[2]))

        async def finish_job_generation(
            self, workspace: str, generation: VectorGenerationMetadata
        ) -> None:
            assert len(generation.indexes) == 2
            self.finished = True

    vectors = Vectors()
    monkeypatch.setattr(processing, "get_metadata_engine", lambda: repository.engine)
    monkeypatch.setattr(processing, "get_source_repository", lambda: source_repository)
    monkeypatch.setattr(processing, "get_source_storage", lambda: object_store)
    monkeypatch.setattr(processing, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(processing, "get_embedding_service", Embeddings)
    monkeypatch.setattr(processing, "get_vector_store", lambda: vectors)
    monkeypatch.setattr(processing, "get_lexical_store", lambda: lexical)
    monkeypatch.setattr(processing, "get_persistence_repository", lambda: coordinator)
    monkeypatch.setattr(processing, "get_csv_ingestion_service", get_csv_ingestion_service)
    job_id = str(uuid4())
    path = f"workspaces/client/uploads/{job_id}/upload.{kind}"
    blobs.values[path] = (
        b"id,text\n1,Policy requires annual review.\n2,Keep records for seven years.\n"
    )
    if kind == "pdf":
        import pymupdf

        pdf = pymupdf.open()
        pdf.new_page().insert_text(
            (72, 72), "Policy requires annual review. Keep records for seven years."
        )
        blobs.values[path] = pdf.tobytes()
        pdf.close()
    await repository.create(
        "client",
        {
            "kind": kind,
            "filename": f"upload.{kind}",
            "pathname": path,
            "text_columns": ["text"],
            "metadata_columns": ["id"],
            "row_id_column": "id",
        },
        job_id,
    )
    for number in range(10):
        result = await step_job(job_id, number)
        if result["status"] == "complete":
            break
    assert result["status"] == "complete"
    assert vectors.finished
    assert set(vectors.batches) == {"vector", "sentence_window"}
    source = (await source_repository.list("client"))[0]
    assert source.status == SourceStatus.READY
    assert await coordinator.active_generation("client", GenerationKind.RETRIEVAL)
    documents = await object_store.load_documents("client", source.source_id)
    if kind == "csv":
        assert [document.row_id for document in documents] == ["1", "2"]
    else:
        assert documents[0].page_number == 1
    assert (await step_job(job_id, 0))["status"] == "complete"


@pytest.mark.asyncio
async def test_graph_extraction_resumes_and_publishes_one_snapshot(
    repository: JobRepository,
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    from app.graph.indexing import GraphIndexingService
    from app.graph.store import LocalGraphStore
    from app.jobs import graph, processing
    from app.models import NormalizedDocument, Source, SourceConfig, SourceStatus, SourceType
    from app.persistence import GenerationKind, InMemoryPersistenceRepository
    from app.repositories import InMemorySourceRepository
    from app.storage.vercel_blob import VercelBlobSourceStorage
    from tests.test_graph_indexing import DeterministicGraphExtractor
    from tests.test_phase18_hosted import MemoryBlob

    sources = InMemorySourceRepository()
    source = Source(
        workspace_id="client",
        name="Projects",
        config=SourceConfig(source_type=SourceType.PDF),
    )
    await sources.create(source)
    await sources.transition("client", source.source_id, SourceStatus.INDEXING)
    await sources.transition("client", source.source_id, SourceStatus.READY)
    objects = VercelBlobSourceStorage("fake", client=MemoryBlob())
    documents = [
        NormalizedDocument(
            workspace_id="client",
            source_id=source.source_id,
            source_type=SourceType.PDF,
            content=f"Project {i} is part of Research.",
            page_number=i + 1,
        )
        for i in range(2)
    ]
    await objects.save_documents("client", source.source_id, documents)
    coordinator = InMemoryPersistenceRepository()
    graph_store = LocalGraphStore(tmp_path / "graph")
    extractor = DeterministicGraphExtractor()
    service = GraphIndexingService(
        sources,
        objects,
        extractor,
        graph_store,
        batch_size=1,
        max_batch_characters=2000,
        persistence_repository=coordinator,
    )
    monkeypatch.setattr(processing, "get_metadata_engine", lambda: repository.engine)
    monkeypatch.setattr(processing, "get_source_storage", lambda: objects)
    monkeypatch.setattr(graph, "get_source_repository", lambda: sources)
    monkeypatch.setattr(graph, "get_graph_indexing_service", lambda: service)
    monkeypatch.setattr(graph, "get_graph_store", lambda: graph_store)
    monkeypatch.setattr(graph, "get_persistence_repository", lambda: coordinator)
    job_id = str(uuid4())
    await repository.create("client", {"kind": "graph"}, job_id)
    await step_job(job_id, 0)
    await step_job(job_id, 1)
    await step_job(job_id, 1)  # Duplicate delivery must not call the model again.
    assert extractor.batch_sizes == [1]
    await step_job(job_id, 2)
    result = await step_job(job_id, 3)
    assert result["status"] == "complete"
    assert extractor.batch_sizes == [1, 1]
    assert await coordinator.active_generation("client", GenerationKind.GRAPH)

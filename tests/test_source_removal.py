"""Removing a source: registry, stored artifacts, permissions, audit, and retrieval scope."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routes import sources as source_routes
from app.api.routes.sources import delete_source
from app.auth.organizations import Role
from app.generation.extractive import ExtractiveGenerator
from app.models import Classification, QueryRequest, SourceType, SourceUpdate
from app.repositories import InMemorySourceRepository
from app.retrieval.indexing import VectorIndexingService
from app.retrieval.query_service import QueryService
from app.retrieval.sentence_window import SentenceWindowRetriever
from app.retrieval.vector import VectorRetriever
from app.retrieval.vector_store import FaissVectorStore
from app.storage import LocalSourceStorage
from tests.test_baseline_rag import FixedEmbeddings, _ready_source


class _Membership:
    def __init__(self, role: Role, clearance: Classification = Classification.RESTRICTED) -> None:
        self.role = role
        self.clearance = clearance
        self.org_id = "org"
        self.workspace_id = "workspace"

    @property
    def effective_clearance(self) -> Classification:
        return self.clearance


def _request(membership: _Membership | None = None) -> Any:
    return SimpleNamespace(state=SimpleNamespace(membership=membership))


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Route settings with auth off and memory metadata unless a test changes them."""
    values = SimpleNamespace(auth_enabled=False, metadata_store_backend="memory")
    monkeypatch.setattr(source_routes, "get_settings", lambda: values)
    return values


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []

    async def record(request: Any, action: str, **fields: Any) -> None:
        del request
        events.append((action, fields))

    monkeypatch.setattr("app.audit.record", record)
    return events


def _query_service(
    repository: InMemorySourceRepository, embeddings: FixedEmbeddings, store: FaissVectorStore
) -> QueryService:
    return QueryService(
        repository,
        VectorRetriever(embeddings, store),
        SentenceWindowRetriever(embeddings, store),
        ExtractiveGenerator(),
        default_top_k=5,
        max_top_k=20,
        min_similarity=0.7,
    )


@pytest.mark.asyncio
async def test_delete_source_RemovesRegistryEntryAndStoredArtifacts(
    tmp_path: Path, settings: SimpleNamespace, audited: list[Any]
) -> None:
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path)
    source = await _ready_source(repository, storage, "workspace", SourceType.PDF, "pdf evidence")
    directory = tmp_path / "workspaces" / "workspace" / "sources" / str(source.source_id)
    assert directory.is_dir()

    await delete_source(source.source_id, "workspace", _request(), repository, storage)

    assert list(await repository.list("workspace")) == []
    assert not directory.exists()
    assert audited[0][0] == "source.deleted"
    assert audited[0][1]["metadata"] == {"name": source.name, "type": "pdf"}


@pytest.mark.asyncio
async def test_delete_source_UnknownOrOtherWorkspace_Returns404(
    tmp_path: Path, settings: SimpleNamespace, audited: list[Any]
) -> None:
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path)
    source = await _ready_source(repository, storage, "other", SourceType.PDF, "pdf evidence")

    for source_id in (uuid4(), source.source_id):
        with pytest.raises(HTTPException) as error:
            await delete_source(source_id, "workspace", _request(), repository, storage)
        assert error.value.status_code == 404
    assert len(await repository.list("other")) == 1
    assert audited == []


@pytest.mark.asyncio
async def test_delete_source_AboveCallerClearance_Returns404(
    tmp_path: Path, settings: SimpleNamespace, audited: list[Any]
) -> None:
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path)
    source = await _ready_source(repository, storage, "workspace", SourceType.PDF, "pdf evidence")
    await repository.update(
        "workspace", source.source_id, SourceUpdate(classification=Classification.CONFIDENTIAL)
    )
    settings.auth_enabled = True
    caller = _Membership(Role.ADMIN, clearance=Classification.INTERNAL)

    with pytest.raises(HTTPException) as error:
        await delete_source(source.source_id, "workspace", _request(caller), repository, storage)

    assert error.value.status_code == 404
    assert len(await repository.list("workspace")) == 1


@pytest.mark.asyncio
async def test_delete_source_MemberRole_Returns403(
    tmp_path: Path, settings: SimpleNamespace, audited: list[Any]
) -> None:
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path)
    source = await _ready_source(repository, storage, "workspace", SourceType.PDF, "pdf evidence")
    settings.auth_enabled = True

    with pytest.raises(HTTPException) as error:
        await delete_source(
            source.source_id, "workspace", _request(_Membership(Role.MEMBER)), repository, storage
        )
    assert error.value.status_code == 403
    assert len(await repository.list("workspace")) == 1

    await delete_source(
        source.source_id, "workspace", _request(_Membership(Role.ADMIN)), repository, storage
    )
    assert list(await repository.list("workspace")) == []


@pytest.mark.asyncio
async def test_delete_source_UnfinishedJob_Returns409(
    tmp_path: Path,
    settings: SimpleNamespace,
    audited: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path)
    source = await _ready_source(repository, storage, "workspace", SourceType.PDF, "pdf evidence")
    settings.metadata_store_backend = "postgresql"
    statuses = ["complete", "running"]

    class FakeJobs:
        def __init__(self, engine: Any) -> None:
            del engine

        async def list(self, workspace_id: str) -> list[dict[str, str]]:
            assert workspace_id == "workspace"
            return [{"status": value} for value in statuses]

    monkeypatch.setattr("app.jobs.repository.JobRepository", FakeJobs)
    monkeypatch.setattr("app.api.dependencies.get_metadata_engine", lambda: object())

    with pytest.raises(HTTPException) as error:
        await delete_source(source.source_id, "workspace", _request(), repository, storage)
    assert error.value.status_code == 409
    assert len(await repository.list("workspace")) == 1

    statuses[:] = ["complete", "failed", "cancelled"]
    await delete_source(source.source_id, "workspace", _request(), repository, storage)
    assert list(await repository.list("workspace")) == []


@pytest.mark.asyncio
async def test_delete_source_StorageFailure_StillRemovesSource(
    tmp_path: Path, settings: SimpleNamespace, audited: list[Any]
) -> None:
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path)
    source = await _ready_source(repository, storage, "workspace", SourceType.PDF, "pdf evidence")

    class BrokenStorage:
        async def delete_source(self, *_: Any) -> None:
            raise OSError("disk unavailable")

    await delete_source(source.source_id, "workspace", _request(), repository, BrokenStorage())  # type: ignore[arg-type]

    assert list(await repository.list("workspace")) == []


@pytest.mark.asyncio
async def test_query_AfterRemoval_DoesNotCiteRemovedSource(
    tmp_path: Path, settings: SimpleNamespace, audited: list[Any]
) -> None:
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path)
    kept = await _ready_source(repository, storage, "workspace", SourceType.PDF, "pdf evidence")
    removed = await _ready_source(
        repository, storage, "workspace", SourceType.WEBSITE, "website evidence"
    )
    embeddings = FixedEmbeddings()
    store = FaissVectorStore(tmp_path)
    await VectorIndexingService(repository, storage, embeddings, store).rebuild(
        "workspace", kept.source_id
    )
    service = _query_service(repository, embeddings, store)
    before = await service.query(QueryRequest(workspace_id="workspace", question="What?"))
    assert {c.source_id for c in before.citations} == {kept.source_id, removed.source_id}

    # Full clearance and no source filter: the case that used to search the index unfiltered.
    await delete_source(removed.source_id, "workspace", _request(), repository, storage)
    after = await service.query(QueryRequest(workspace_id="workspace", question="What?"))
    assert {c.source_id for c in after.citations} == {kept.source_id}

    await delete_source(kept.source_id, "workspace", _request(), repository, storage)
    empty = await service.query(QueryRequest(workspace_id="workspace", question="What?"))
    assert empty.insufficient_evidence
    assert empty.citations == []


@pytest.mark.asyncio
async def test_query_MemoryRegistryAfterRestart_UsesStoredInventory(tmp_path: Path) -> None:
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path)
    source = await _ready_source(repository, storage, "workspace", SourceType.PDF, "pdf evidence")
    embeddings = FixedEmbeddings()
    store = FaissVectorStore(tmp_path)
    await VectorIndexingService(repository, storage, embeddings, store).rebuild(
        "workspace", source.source_id
    )
    restarted = InMemorySourceRepository()
    service = QueryService(
        restarted,
        VectorRetriever(embeddings, store),
        SentenceWindowRetriever(embeddings, store),
        ExtractiveGenerator(),
        default_top_k=5,
        max_top_k=20,
        min_similarity=0.7,
        inventory_storage=storage,
    )

    response = await service.query(QueryRequest(workspace_id="workspace", question="What?"))
    assert [c.source_id for c in response.citations] == [source.source_id]

    await storage.delete_source("workspace", source.source_id)
    gone = await service.query(QueryRequest(workspace_id="workspace", question="What?"))
    assert gone.citations == []


@pytest.mark.asyncio
async def test_delete_source_LocalMissingDirectory_IsNotAnError(tmp_path: Path) -> None:
    await LocalSourceStorage(tmp_path).delete_source("workspace", uuid4())

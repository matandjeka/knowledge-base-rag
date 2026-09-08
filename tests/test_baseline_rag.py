"""Phase 5 baseline vector retrieval, citation, and indexing tests."""

from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from numpy.typing import NDArray

from app.api.dependencies import get_query_service
from app.api.routes.sources import index_source
from app.core.exceptions import IndexingError, IndexNotFoundError
from app.generation.extractive import INSUFFICIENT_EVIDENCE_ANSWER, ExtractiveGenerator
from app.generation.prompt import build_grounded_prompt
from app.main import app
from app.models import (
    NormalizedDocument,
    QueryRequest,
    QueryResponse,
    Source,
    SourceConfig,
    SourceStatus,
    SourceType,
)
from app.repositories import InMemorySourceRepository
from app.retrieval.embedding import HuggingFaceEmbeddingService
from app.retrieval.indexing import VectorIndexingService
from app.retrieval.query_service import QueryService
from app.retrieval.sentence_window import SentenceWindowRetriever
from app.retrieval.vector import VectorRetriever
from app.retrieval.vector_store import FaissVectorStore, VectorIndexMetadata
from app.storage import LocalSourceStorage


class FixedEmbeddings:
    """Small deterministic embedding service for retrieval tests."""

    model_name = "fixed-test"
    dimension = 3

    async def embed_documents(self, texts: Sequence[str]) -> NDArray[np.float32]:
        values = {
            "pdf evidence": [1.0, 0.0, 0.0],
            "website evidence": [0.9, 0.1, 0.0],
            "csv evidence": [0.8, 0.2, 0.0],
        }
        return np.asarray([values[text] for text in texts], dtype=np.float32)

    async def embed_query(self, text: str) -> NDArray[np.float32]:
        del text
        return np.asarray([1.0, 0.0, 0.0], dtype=np.float32)


class RecordingModel:
    """Sentence Transformer stand-in that records adapter inputs and options."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def encode(self, texts: list[str], **options: object) -> NDArray[np.float32]:
        self.calls.append((texts, options))
        return np.asarray([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32)


def _document(source: Source, content: str) -> NormalizedDocument:
    locators: dict[SourceType, dict[str, object]] = {
        SourceType.PDF: {"page_number": 2},
        SourceType.WEBSITE: {"source_uri": "https://example.test/guide"},
        SourceType.CSV: {"row_id": "row-7"},
    }
    return NormalizedDocument(
        workspace_id=source.workspace_id,
        source_id=source.source_id,
        source_type=source.config.source_type,
        title=source.name,
        content=content,
        **locators[source.config.source_type],
    )


async def _ready_source(
    repository: InMemorySourceRepository,
    storage: LocalSourceStorage,
    workspace_id: str,
    source_type: SourceType,
    content: str,
) -> Source:
    source = Source(
        workspace_id=workspace_id,
        name=f"{source_type.value} source",
        config=SourceConfig(source_type=source_type),
    )
    await repository.create(source)
    await repository.transition(workspace_id, source.source_id, SourceStatus.INDEXING)
    source = await repository.transition(workspace_id, source.source_id, SourceStatus.READY)
    await storage.save_source(source)
    await storage.save_documents(workspace_id, source.source_id, [_document(source, content)])
    return source


@pytest.mark.asyncio
async def test_query_retrieves_and_cites_all_three_source_types(tmp_path: Path) -> None:
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path)
    workspace_id = "workspace"
    sources = [
        await _ready_source(repository, storage, workspace_id, SourceType.PDF, "pdf evidence"),
        await _ready_source(
            repository, storage, workspace_id, SourceType.WEBSITE, "website evidence"
        ),
        await _ready_source(repository, storage, workspace_id, SourceType.CSV, "csv evidence"),
    ]
    embeddings = FixedEmbeddings()
    vector_store = FaissVectorStore(tmp_path)
    indexer = VectorIndexingService(repository, storage, embeddings, vector_store)
    await indexer.rebuild(workspace_id, sources[0].source_id)
    service = QueryService(
        repository,
        VectorRetriever(embeddings, vector_store),
        SentenceWindowRetriever(embeddings, vector_store),
        ExtractiveGenerator(),
        default_top_k=5,
        max_top_k=20,
        min_similarity=0.7,
    )

    response = await service.query(QueryRequest(workspace_id=workspace_id, question="What?"))

    assert [citation.citation_id for citation in response.citations] == ["S1", "S2", "S3"]
    assert {citation.locator for citation in response.citations} == {
        "page 2",
        "https://example.test/guide",
        "row row-7",
    }
    assert not response.insufficient_evidence

    filtered = await service.query(
        QueryRequest(
            workspace_id=workspace_id,
            question="What?",
            source_ids=[sources[2].source_id],
        )
    )
    assert [citation.source_id for citation in filtered.citations] == [sources[2].source_id]


@pytest.mark.asyncio
async def test_query_returns_insufficient_evidence_below_threshold(tmp_path: Path) -> None:
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path)
    source = await _ready_source(repository, storage, "workspace", SourceType.PDF, "pdf evidence")
    embeddings = FixedEmbeddings()
    vector_store = FaissVectorStore(tmp_path)
    await VectorIndexingService(repository, storage, embeddings, vector_store).rebuild(
        "workspace", source.source_id
    )
    service = QueryService(
        repository,
        VectorRetriever(embeddings, vector_store),
        SentenceWindowRetriever(embeddings, vector_store),
        ExtractiveGenerator(),
        default_top_k=5,
        max_top_k=20,
        min_similarity=1.1,
    )

    response = await service.query(QueryRequest(workspace_id="workspace", question="Unknown?"))

    assert response.insufficient_evidence
    assert response.answer == INSUFFICIENT_EVIDENCE_ANSWER
    assert response.citations == []


@pytest.mark.asyncio
async def test_rebuild_uses_persisted_ready_sources_after_registry_restart(tmp_path: Path) -> None:
    storage = LocalSourceStorage(tmp_path)
    old_repository = InMemorySourceRepository()
    await _ready_source(old_repository, storage, "workspace", SourceType.PDF, "pdf evidence")
    new_repository = InMemorySourceRepository()
    target = Source(
        workspace_id="workspace",
        name="new website",
        config=SourceConfig(source_type=SourceType.WEBSITE),
    )
    await new_repository.create(target)
    await new_repository.transition("workspace", target.source_id, SourceStatus.INDEXING)
    await storage.save_documents(
        "workspace", target.source_id, [_document(target, "website evidence")]
    )
    indexer = VectorIndexingService(
        new_repository, storage, FixedEmbeddings(), FaissVectorStore(tmp_path)
    )

    metadata = await indexer.prepare("workspace", target.source_id)

    assert metadata.document_count == 2


@pytest.mark.asyncio
async def test_explicit_index_route_maps_indexing_error_to_http_500() -> None:
    repository = InMemorySourceRepository()
    source = Source(
        workspace_id="workspace",
        name="source",
        config=SourceConfig(source_type=SourceType.PDF),
    )
    await repository.create(source)

    class BrokenIndexer:
        async def rebuild(self, workspace_id: str, source_id: UUID) -> VectorIndexMetadata:
            del workspace_id, source_id
            raise IndexingError("index failed")

    with pytest.raises(HTTPException) as captured:
        await index_source(source.source_id, "workspace", repository, BrokenIndexer())  # type: ignore[arg-type]

    assert captured.value.status_code == 500
    assert captured.value.detail == "index failed"


def test_grounded_prompt_contains_question_evidence_and_rules() -> None:
    source_id = uuid4()
    source = Source(
        source_id=source_id,
        workspace_id="workspace",
        name="manual",
        config=SourceConfig(source_type=SourceType.PDF),
    )
    evidence = [_document(source, "pdf evidence")]
    from app.citations.builder import build_citations
    from app.models import Evidence

    retrieved = [
        Evidence(
            retriever="vector",
            content=evidence[0].content,
            source_id=source_id,
            source_type=SourceType.PDF,
            page_number=2,
            raw_score=1.0,
        )
    ]
    prompt = build_grounded_prompt("What is documented?", retrieved, build_citations(retrieved))

    assert "What is documented?" in prompt
    assert "[S1] pdf evidence" in prompt
    assert "Answer only from the supplied evidence" in prompt


@pytest.mark.asyncio
async def test_hugging_face_adapter_prefixes_only_queries_and_requests_normalization() -> None:
    model = RecordingModel()
    embeddings = HuggingFaceEmbeddingService(
        model_name="test-model", dimension=3, batch_size=7, query_prefix="query: "
    )
    embeddings._model = model

    documents = await embeddings.embed_documents(["document"])
    query = await embeddings.embed_query("question")

    assert documents.shape == (1, 3)
    assert query.shape == (3,)
    assert model.calls[0][0] == ["document"]
    assert model.calls[1][0] == ["query: question"]
    assert all(call[1]["normalize_embeddings"] is True for call in model.calls)
    assert all(call[1]["batch_size"] == 7 for call in model.calls)


@pytest.mark.asyncio
async def test_faiss_generation_is_not_searchable_until_activated(tmp_path: Path) -> None:
    store = FaissVectorStore(tmp_path)
    source = Source(
        workspace_id="workspace",
        name="source",
        config=SourceConfig(source_type=SourceType.PDF),
    )
    document = _document(source, "pdf evidence")
    vector = np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32)
    metadata = await store.prepare(
        "workspace", [document], vector, model_name="fixed-test", dimension=3
    )

    with pytest.raises(IndexNotFoundError):
        await store.search(
            "workspace",
            vector[0],
            top_k=1,
            source_ids=frozenset(),
            model_name="fixed-test",
            dimension=3,
        )

    await store.activate("workspace", metadata.generation_id)
    matches = await store.search(
        "workspace",
        vector[0],
        top_k=1,
        source_ids=frozenset(),
        model_name="fixed-test",
        dimension=3,
    )
    assert [match.document.document_id for match in matches] == [document.document_id]


@pytest.mark.asyncio
async def test_faiss_rejects_corrupted_document_mapping(tmp_path: Path) -> None:
    store = FaissVectorStore(tmp_path)
    source = Source(
        workspace_id="workspace",
        name="source",
        config=SourceConfig(source_type=SourceType.PDF),
    )
    document = _document(source, "pdf evidence")
    vector = np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32)
    metadata = await store.rebuild(
        "workspace", [document], vector, model_name="fixed-test", dimension=3
    )
    mapping = (
        tmp_path
        / "vector-indexes"
        / "workspaces"
        / "workspace"
        / "generations"
        / str(metadata.generation_id)
        / "vector"
        / "documents.json"
    )
    mapping.write_text("[]")

    with pytest.raises(IndexingError, match="checksum"):
        await store.search(
            "workspace",
            vector[0],
            top_k=1,
            source_ids=frozenset(),
            model_name="fixed-test",
            dimension=3,
        )


@pytest.mark.asyncio
async def test_query_endpoint_returns_contract_and_maps_missing_index() -> None:
    class StubQueryService:
        async def query(self, request: QueryRequest, **_kwargs: object) -> QueryResponse:
            if request.question == "missing":
                raise IndexNotFoundError("No vector index exists for this workspace")
            return QueryResponse(answer="No evidence", insufficient_evidence=True)

    app.dependency_overrides[get_query_service] = lambda: StubQueryService()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/query", json={"workspace_id": "workspace", "question": "question"}
            )
            missing = await client.post(
                "/query", json={"workspace_id": "workspace", "question": "missing"}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "answer": "No evidence",
        "citations": [],
        "evidence": [],
        "insufficient_evidence": True,
    }
    assert missing.status_code == 409

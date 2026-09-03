"""Phase 7 graph extraction, indexing orchestration, and API tests."""

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_graph_indexing_service
from app.core.exceptions import IndexingError
from app.generation.extractive import ExtractiveGenerator
from app.graph.indexing import GraphIndexingService
from app.graph.openai_extractor import OpenAIGraphExtractor
from app.graph.store import LocalGraphStore
from app.main import app
from app.models import (
    BatchGraphExtraction,
    DocumentGraphExtraction,
    EntityType,
    Evidence,
    ExtractedEntity,
    ExtractedRelationship,
    GraphIndexResult,
    NormalizedDocument,
    QueryRequest,
    RelationshipType,
    RetrievalMode,
    Source,
    SourceConfig,
    SourceStatus,
    SourceType,
)
from app.repositories import InMemorySourceRepository
from app.retrieval.query_service import QueryService
from app.storage import LocalSourceStorage


class DeterministicGraphExtractor:
    model_name = "deterministic-graph"
    prompt_version = "1"

    def __init__(self, *, fail: bool = False) -> None:
        self.batch_sizes: list[int] = []
        self.fail = fail

    async def extract(
        self, documents: Sequence[NormalizedDocument]
    ) -> list[DocumentGraphExtraction]:
        self.batch_sizes.append(len(documents))
        if self.fail:
            raise IndexingError("extractor failed")
        results = []
        for document in documents:
            project, department = document.content.removesuffix(".").split(" is part of ")
            results.append(
                DocumentGraphExtraction(
                    document_id=document.document_id,
                    entities=[
                        ExtractedEntity(
                            reference="project",
                            entity_type=EntityType.PROJECT,
                            canonical_name=project,
                            supporting_text=project,
                        ),
                        ExtractedEntity(
                            reference="department",
                            entity_type=EntityType.DEPARTMENT,
                            canonical_name=department,
                            supporting_text=department,
                        ),
                    ],
                    relationships=[
                        ExtractedRelationship(
                            subject_reference="project",
                            predicate=RelationshipType.PART_OF,
                            object_reference="department",
                            confidence=1,
                            supporting_text=document.content,
                        )
                    ],
                )
            )
        return results


async def _persist_ready_documents(storage: LocalSourceStorage, count: int = 3) -> None:
    for index in range(count):
        source = Source(
            workspace_id="workspace",
            name=f"source {index}",
            config=SourceConfig(source_type=SourceType.PDF),
            status=SourceStatus.READY,
        )
        await storage.save_source(source)
        await storage.save_documents(
            "workspace",
            source.source_id,
            [
                NormalizedDocument(
                    workspace_id="workspace",
                    source_id=source.source_id,
                    source_type=SourceType.PDF,
                    content=f"Project {index} is part of Department {index}.",
                    page_number=index + 1,
                )
            ],
        )


@pytest.mark.asyncio
async def test_graph_indexing_batches_all_ready_documents_and_activates(tmp_path: Path) -> None:
    storage = LocalSourceStorage(tmp_path)
    await _persist_ready_documents(storage)
    failed_source = Source(
        workspace_id="workspace",
        name="failed source",
        config=SourceConfig(source_type=SourceType.PDF),
        status=SourceStatus.FAILED,
    )
    await storage.save_source(failed_source)
    await storage.save_documents(
        "workspace",
        failed_source.source_id,
        [
            NormalizedDocument(
                workspace_id="workspace",
                source_id=failed_source.source_id,
                source_type=SourceType.PDF,
                content="This non-ready document must never reach extraction.",
                page_number=99,
            )
        ],
    )
    extractor = DeterministicGraphExtractor()
    store = LocalGraphStore(tmp_path)
    service = GraphIndexingService(
        storage, extractor, store, batch_size=2, max_batch_characters=10_000
    )

    result = await service.rebuild("workspace")
    snapshot = await store.load("workspace")

    assert extractor.batch_sizes == [2, 1]
    assert result.source_count == 3
    assert result.document_count == 3
    assert result.entity_count == 6
    assert result.relationship_count == 3
    assert snapshot.generation_id == result.generation_id
    assert failed_source.source_id not in snapshot.source_ids


@pytest.mark.asyncio
async def test_failed_rebuild_keeps_previous_graph_active(tmp_path: Path) -> None:
    storage = LocalSourceStorage(tmp_path)
    await _persist_ready_documents(storage, count=1)
    store = LocalGraphStore(tmp_path)
    successful = GraphIndexingService(
        storage,
        DeterministicGraphExtractor(),
        store,
        batch_size=2,
        max_batch_characters=10_000,
    )
    original = await successful.rebuild("workspace")
    broken = GraphIndexingService(
        storage,
        DeterministicGraphExtractor(fail=True),
        store,
        batch_size=2,
        max_batch_characters=10_000,
    )

    with pytest.raises(IndexingError, match="extractor failed"):
        await broken.rebuild("workspace")

    assert (await store.load("workspace")).generation_id == original.generation_id


@pytest.mark.asyncio
async def test_openai_extractor_uses_strict_parsed_output() -> None:
    document = NormalizedDocument(
        workspace_id="workspace",
        source_id=uuid4(),
        source_type=SourceType.PDF,
        content="Project Atlas is part of Operations.",
        page_number=1,
    )
    parsed = BatchGraphExtraction(
        documents=[
            DocumentGraphExtraction(
                document_id=document.document_id,
                entities=[],
                relationships=[],
            )
        ]
    )

    class Responses:
        def __init__(self) -> None:
            self.options: dict[str, object] = {}

        async def parse(self, **options: object) -> object:
            self.options = options
            return SimpleNamespace(output_parsed=parsed)

    responses = Responses()
    client = SimpleNamespace(responses=responses)
    extractor = OpenAIGraphExtractor(
        api_key="test-secret",
        model_name="test-model",
        timeout_seconds=5,
        max_retries=0,
        client=client,
    )

    result = await extractor.extract([document])

    assert result == parsed.documents
    assert responses.options["model"] == "test-model"
    assert responses.options["text_format"] is BatchGraphExtraction
    assert "test-secret" not in str(responses.options)


@pytest.mark.asyncio
async def test_openai_extractor_rejects_missing_structured_output() -> None:
    class Responses:
        async def parse(self, **options: object) -> object:
            del options
            return SimpleNamespace(output_parsed=None)

    extractor = OpenAIGraphExtractor(
        api_key="test-secret",
        model_name="test-model",
        timeout_seconds=5,
        max_retries=0,
        client=SimpleNamespace(responses=Responses()),
    )
    document = NormalizedDocument(
        workspace_id="workspace",
        source_id=uuid4(),
        source_type=SourceType.PDF,
        content="Project Atlas is part of Operations.",
        page_number=1,
    )

    with pytest.raises(IndexingError, match="no structured output"):
        await extractor.extract([document])


@pytest.mark.asyncio
async def test_graph_index_endpoint_returns_generation_summary() -> None:
    expected = GraphIndexResult(
        workspace_id="workspace",
        generation_id=uuid4(),
        source_count=2,
        document_count=3,
        entity_count=4,
        relationship_count=2,
    )

    class StubIndexer:
        async def rebuild(self, workspace_id: str) -> GraphIndexResult:
            assert workspace_id == "workspace"
            return expected

    app.dependency_overrides[get_graph_indexing_service] = lambda: StubIndexer()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/graph/index", json={"workspace_id": "workspace"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")


@pytest.mark.asyncio
async def test_query_service_dispatches_graph_mode_through_common_contract() -> None:
    calls: list[str] = []

    class StubRetriever:
        def __init__(self, name: str) -> None:
            self.name = name

        async def retrieve(
            self,
            workspace_id: str,
            query: str,
            *,
            top_k: int,
            source_ids: frozenset[UUID],
            min_similarity: float,
        ) -> list[Evidence]:
            del workspace_id, query, top_k, source_ids, min_similarity
            calls.append(self.name)
            if self.name != "graph":
                return []
            return [
                Evidence(
                    retriever="vector",
                    content="Graph path",
                    source_id=uuid4(),
                    source_type=SourceType.PDF,
                    page_number=1,
                    raw_score=1,
                )
            ]

    service = QueryService(
        InMemorySourceRepository(),
        StubRetriever("vector"),
        StubRetriever("window"),
        ExtractiveGenerator(),
        default_top_k=3,
        max_top_k=20,
        min_similarity=0.7,
        graph_retriever=StubRetriever("graph"),
    )

    response = await service.query(
        QueryRequest(
            workspace_id="workspace",
            question="Who owns Project Atlas?",
            retrieval_mode=RetrievalMode.GRAPH,
        )
    )

    assert calls == ["graph"]
    assert not response.insufficient_evidence

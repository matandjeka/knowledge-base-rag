"""Phase 8 BM25 indexing, retrieval, and benchmark tests."""

from collections.abc import Sequence
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

import numpy as np
import pytest
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, TypeAdapter

from app.citations.builder import build_citations
from app.core.exceptions import IndexingError, IndexNotFoundError
from app.evaluation.retrieval_comparison import (
    RetrievalBenchmarkCase,
    RetrievalBenchmarkRun,
    score_retrieval_runs,
)
from app.generation.extractive import ExtractiveGenerator
from app.models import (
    NormalizedDocument,
    QueryRequest,
    RetrievalMode,
    Source,
    SourceConfig,
    SourceStatus,
    SourceType,
)
from app.repositories import InMemorySourceRepository
from app.retrieval.indexing import VectorIndexingService
from app.retrieval.lexical import LexicalRetriever, LocalLexicalStore, tokenize_lexical
from app.retrieval.query_service import QueryService
from app.retrieval.vector import VectorRetriever
from app.retrieval.vector_store import FaissVectorStore
from app.storage import LocalSourceStorage


class LexicalFixture(BaseModel):
    """Committed identifier-focused retrieval case."""

    model_config = ConfigDict(extra="forbid")

    key: str
    source_type: SourceType
    content: str
    question: str
    page_number: int | None = None
    source_uri: str | None = None
    row_id: str | None = None


class FlatEmbeddings:
    """Make semantic ranking intentionally unable to distinguish identifiers."""

    model_name = "flat-test"
    dimension = 3

    async def embed_documents(self, texts: Sequence[str]) -> NDArray[np.float32]:
        return np.asarray([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32)

    async def embed_query(self, text: str) -> NDArray[np.float32]:
        return np.asarray([1.0, 0.0, 0.0], dtype=np.float32)


def _document(
    source_id: UUID,
    content: str,
    *,
    source_type: SourceType = SourceType.PDF,
    title: str | None = None,
) -> NormalizedDocument:
    return NormalizedDocument(
        workspace_id="workspace",
        source_id=source_id,
        source_type=source_type,
        title=title,
        content=content,
        page_number=1 if source_type is SourceType.PDF else None,
        source_uri="https://example.test/item" if source_type is SourceType.WEBSITE else None,
        row_id="row-1" if source_type is SourceType.CSV else None,
    )


def test_tokenizer_preserves_identifiers_and_normalizes_unicode_case() -> None:
    assert tokenize_lexical("Policy HR-402 and SKU_88Z — CAFÉ") == (
        "policy",
        "hr-402",
        "and",
        "sku_88z",
        "café",
    )


@pytest.mark.asyncio
async def test_lexical_store_requires_activation_and_ranks_exact_identifier(tmp_path: Path) -> None:
    store = LocalLexicalStore(tmp_path, k1=1.2, b=0.7)
    target_source = uuid4()
    other_source = uuid4()
    target = _document(target_source, "Policy HR-402 requires annual review.")
    other = _document(other_source, "The general policy requires periodic review.")
    metadata = await store.prepare("workspace", [other, target])

    with pytest.raises(IndexNotFoundError):
        await store.search(
            "workspace",
            "HR-402",
            top_k=2,
            source_ids=frozenset(),
            min_score=0,
            title_boost=0.5,
        )

    await store.activate("workspace", metadata.generation_id)
    matches = await store.search(
        "workspace",
        "HR-402",
        top_k=2,
        source_ids=frozenset(),
        min_score=0,
        title_boost=0.5,
    )

    assert [match.document.document_id for match in matches] == [target.document_id]
    assert matches[0].score > 0
    assert matches[0].normalized_score == 1

    assert (
        await store.search(
            "workspace",
            "UNKNOWN-999",
            top_k=2,
            source_ids=frozenset(),
            min_score=0,
            title_boost=0.5,
        )
        == ()
    )


@pytest.mark.asyncio
async def test_lexical_ranking_is_deterministic_and_title_boost_is_effective(
    tmp_path: Path,
) -> None:
    store = LocalLexicalStore(tmp_path)
    source_id = uuid4()
    lower_id = UUID("00000000-0000-0000-0000-000000000001")
    higher_id = UUID("00000000-0000-0000-0000-000000000002")
    title_match = _document(
        source_id,
        "Reference material for the control.",
        title="HR-402",
    ).model_copy(update={"document_id": higher_id})
    body_match = _document(source_id, "HR-402 reference material for the control.").model_copy(
        update={"document_id": lower_id}
    )
    metadata = await store.prepare("workspace", [title_match, body_match])
    await store.activate("workspace", metadata.generation_id)

    first = await store.search(
        "workspace",
        "HR-402",
        top_k=2,
        source_ids=frozenset(),
        min_score=0,
        title_boost=2,
    )
    second = await store.search(
        "workspace",
        "HR-402",
        top_k=2,
        source_ids=frozenset(),
        min_score=0,
        title_boost=2,
    )

    assert first[0].document.document_id == higher_id
    assert [item.document.document_id for item in first] == [
        item.document.document_id for item in second
    ]


@pytest.mark.asyncio
async def test_lexical_retriever_filters_sources_and_preserves_locators(tmp_path: Path) -> None:
    store = LocalLexicalStore(tmp_path)
    pdf_source = uuid4()
    csv_source = uuid4()
    pdf = _document(pdf_source, "Asset ZX-81 is described here.")
    csv = _document(csv_source, "ZX-81 | available", source_type=SourceType.CSV)
    metadata = await store.prepare("workspace", [pdf, csv])
    await store.activate("workspace", metadata.generation_id)

    evidence = await LexicalRetriever(store).retrieve(
        "workspace",
        "ZX-81",
        top_k=5,
        source_ids=frozenset({csv_source}),
        min_similarity=0,
    )

    assert len(evidence) == 1
    assert evidence[0].retriever == "lexical"
    assert evidence[0].source_id == csv_source
    assert evidence[0].row_id == "row-1"
    assert evidence[0].raw_score is not None
    assert evidence[0].normalized_score == 1


@pytest.mark.asyncio
async def test_lexical_store_detects_corrupt_index(tmp_path: Path) -> None:
    store = LocalLexicalStore(tmp_path)
    metadata = await store.prepare("workspace", [_document(uuid4(), "Policy HR-402")])
    await store.activate("workspace", metadata.generation_id)
    index_path = (
        tmp_path
        / "lexical-indexes"
        / "workspaces"
        / "workspace"
        / "generations"
        / str(metadata.generation_id)
        / "index.json"
    )
    index_path.write_text("{}")

    with pytest.raises(IndexingError, match="checksum"):
        await store.search(
            "workspace",
            "HR-402",
            top_k=1,
            source_ids=frozenset(),
            min_score=0,
            title_boost=0,
        )


@pytest.mark.asyncio
async def test_indexer_prepares_and_activates_matching_lexical_generation(tmp_path: Path) -> None:
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path)
    source = Source(
        workspace_id="workspace",
        name="policy",
        config=SourceConfig(source_type=SourceType.PDF),
    )
    await repository.create(source)
    await repository.transition("workspace", source.source_id, SourceStatus.INDEXING)
    source = await repository.transition("workspace", source.source_id, SourceStatus.READY)
    await storage.save_source(source)
    await storage.save_documents(
        "workspace", source.source_id, [_document(source.source_id, "Policy HR-402 applies.")]
    )
    lexical_store = LocalLexicalStore(tmp_path)
    indexer = VectorIndexingService(
        repository,
        storage,
        FlatEmbeddings(),
        FaissVectorStore(tmp_path),
        lexical_store=lexical_store,
    )

    metadata = await indexer.rebuild("workspace", source.source_id)
    matches = await lexical_store.search(
        "workspace",
        "HR-402",
        top_k=1,
        source_ids=frozenset(),
        min_score=0,
        title_boost=0,
    )

    assert metadata.generation_id
    assert matches[0].document.source_id == source.source_id


@pytest.mark.asyncio
async def test_query_service_selects_lexical_mode_and_uses_lexical_threshold(
    tmp_path: Path,
) -> None:
    repository = InMemorySourceRepository()
    source = Source(
        workspace_id="workspace",
        name="policy",
        config=SourceConfig(source_type=SourceType.PDF),
    )
    await repository.create(source)
    store = LocalLexicalStore(tmp_path)
    metadata = await store.prepare(
        "workspace", [_document(source.source_id, "Policy HR-402 requires review.")]
    )
    await store.activate("workspace", metadata.generation_id)
    lexical = LexicalRetriever(store)
    service = QueryService(
        repository,
        lexical,
        lexical,
        ExtractiveGenerator(),
        default_top_k=5,
        max_top_k=20,
        min_similarity=0.99,
        lexical_retriever=lexical,
        lexical_min_score=0,
    )

    response = await service.query(
        QueryRequest(
            workspace_id="workspace",
            question="What does HR-402 require?",
            retrieval_mode=RetrievalMode.LEXICAL,
        )
    )

    assert not response.insufficient_evidence
    assert response.evidence[0].retriever == "lexical"


@pytest.mark.asyncio
async def test_identifier_benchmark_lexical_outperforms_vector_only(tmp_path: Path) -> None:
    fixtures = TypeAdapter(list[LexicalFixture]).validate_json(
        (Path(__file__).parent / "fixtures" / "lexical_benchmark.json").read_bytes()
    )
    assert len(fixtures) >= 6
    assert {fixture.source_type for fixture in fixtures} == {
        SourceType.PDF,
        SourceType.WEBSITE,
        SourceType.CSV,
    }
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path)
    sources: list[Source] = []
    for fixture in fixtures:
        source = Source(
            workspace_id="workspace",
            name=f"{fixture.key} reference",
            config=SourceConfig(source_type=fixture.source_type),
        )
        await repository.create(source)
        await repository.transition("workspace", source.source_id, SourceStatus.INDEXING)
        source = await repository.transition("workspace", source.source_id, SourceStatus.READY)
        await storage.save_source(source)
        await storage.save_documents(
            "workspace",
            source.source_id,
            [
                NormalizedDocument(
                    workspace_id="workspace",
                    source_id=source.source_id,
                    source_type=fixture.source_type,
                    title="Enterprise reference",
                    content=fixture.content,
                    page_number=fixture.page_number,
                    source_uri=fixture.source_uri,
                    row_id=fixture.row_id,
                )
            ],
        )
        sources.append(source)
    embeddings = FlatEmbeddings()
    vector_store = FaissVectorStore(tmp_path)
    lexical_store = LocalLexicalStore(tmp_path)
    await VectorIndexingService(
        repository,
        storage,
        embeddings,
        vector_store,
        lexical_store=lexical_store,
    ).rebuild("workspace", sources[0].source_id)
    vector = VectorRetriever(embeddings, vector_store)
    lexical = LexicalRetriever(lexical_store)
    vector_runs: list[RetrievalBenchmarkRun] = []
    lexical_runs: list[RetrievalBenchmarkRun] = []
    for fixture, source in zip(fixtures, sources, strict=True):
        locator = (
            f"page {fixture.page_number}"
            if fixture.page_number is not None
            else fixture.source_uri or f"row {fixture.row_id}"
        )
        case = RetrievalBenchmarkCase(
            question=fixture.question,
            expected_source_id=source.source_id,
            expected_locator=locator,
            required_context=fixture.key,
        )
        vector_started = perf_counter()
        vector_evidence = await vector.retrieve(
            "workspace",
            fixture.question,
            top_k=1,
            source_ids=frozenset(),
            min_similarity=0,
        )
        vector_runs.append(
            RetrievalBenchmarkRun(
                case=case,
                evidence=tuple(vector_evidence),
                citations=tuple(build_citations(vector_evidence)),
                latency_seconds=perf_counter() - vector_started,
            )
        )
        lexical_started = perf_counter()
        lexical_evidence = await lexical.retrieve(
            "workspace",
            fixture.question,
            top_k=1,
            source_ids=frozenset(),
            min_similarity=0,
        )
        lexical_runs.append(
            RetrievalBenchmarkRun(
                case=case,
                evidence=tuple(lexical_evidence),
                citations=tuple(build_citations(lexical_evidence)),
                latency_seconds=perf_counter() - lexical_started,
            )
        )
    vector_metrics = score_retrieval_runs(vector_runs)
    lexical_metrics = score_retrieval_runs(lexical_runs)

    assert lexical_metrics.hit_rate_at_k == 1
    assert lexical_metrics.mean_reciprocal_rank == 1
    assert lexical_metrics.citation_accuracy == 1
    assert lexical_metrics.hit_rate_at_k > vector_metrics.hit_rate_at_k
    assert lexical_metrics.mean_reciprocal_rank > vector_metrics.mean_reciprocal_rank
    assert lexical_metrics.mean_latency_seconds >= 0

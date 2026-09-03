"""Phase 6 sentence parsing, expansion, retrieval, and comparison tests."""

from collections.abc import Sequence
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import numpy as np
import pytest
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, TypeAdapter

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
from app.retrieval.query_service import QueryService
from app.retrieval.sentence_window import (
    SentenceWindowRetriever,
    build_sentence_window_documents,
    parse_sentences,
)
from app.retrieval.vector import VectorRetriever
from app.retrieval.vector_store import (
    FaissVectorStore,
    VectorIndexKind,
    VectorIndexPayload,
)
from app.storage import LocalSourceStorage


class ParagraphComparisonEmbeddings:
    """Favor a focused sentence over the containing paragraph for a benchmark fixture."""

    model_name = "paragraph-comparison"
    dimension = 3

    async def embed_documents(self, texts: Sequence[str]) -> NDArray[np.float32]:
        return np.asarray([self._vector(text) for text in texts], dtype=np.float32)

    async def embed_query(self, text: str) -> NDArray[np.float32]:
        del text
        return np.asarray([1.0, 0.0, 0.0], dtype=np.float32)

    @staticmethod
    def _vector(text: str) -> list[float]:
        if text == "The retention period is seven years.":
            return [1.0, 0.0, 0.0]
        if "The retention period is seven years." in text:
            return [0.6, 0.8, 0.0]
        return [0.0, 1.0, 0.0]


class BenchmarkFixture(BaseModel):
    """One committed sentence-window comparison scenario."""

    model_config = ConfigDict(extra="forbid")

    key: str
    question: str
    before: str
    target: str
    after: str
    page_number: int


class CuratedBenchmarkEmbeddings:
    """Deterministically distinguish sentence matches from their parent paragraphs."""

    model_name = "curated-sentence-window-benchmark"

    def __init__(self, fixtures: list[BenchmarkFixture]) -> None:
        self._fixtures = fixtures
        self._target_positions = {
            fixture.target: position for position, fixture in enumerate(fixtures)
        }
        self._question_positions = {
            fixture.question: position for position, fixture in enumerate(fixtures)
        }
        self.dimension = len(fixtures) + 1

    async def embed_documents(self, texts: Sequence[str]) -> NDArray[np.float32]:
        return np.asarray([self._document_vector(text) for text in texts], dtype=np.float32)

    async def embed_query(self, text: str) -> NDArray[np.float32]:
        vector = [0.0] * self.dimension
        vector[self._question_positions[text]] = 1.0
        return np.asarray(vector, dtype=np.float32)

    def _document_vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        exact_position = self._target_positions.get(text)
        if exact_position is not None:
            vector[exact_position] = 1.0
            return vector
        for target, position in self._target_positions.items():
            if target in text:
                vector[position] = 0.6
                vector[-1] = 0.8
                return vector
        vector[-1] = 1.0
        return vector


def test_sentence_parser_preserves_offsets_and_common_abbreviations() -> None:
    text = (
        "Overview\nDr. Ada approved version 2.5. "
        "The retention period is seven years. Records are then destroyed."
    )

    sentences = parse_sentences(text)

    assert [sentence.text for sentence in sentences] == [
        "Overview",
        "Dr. Ada approved version 2.5.",
        "The retention period is seven years.",
        "Records are then destroyed.",
    ]
    assert all(text[sentence.start : sentence.end] == sentence.text for sentence in sentences)


def test_sentence_parser_preserves_closing_quotes() -> None:
    text = 'The policy says "retain records." Then destroy them.'

    sentences = parse_sentences(text)

    assert [sentence.text for sentence in sentences] == [
        'The policy says "retain records."',
        "Then destroy them.",
    ]
    assert all(text[sentence.start : sentence.end] == sentence.text for sentence in sentences)


def test_window_documents_preserve_parent_boundaries_and_locator_metadata() -> None:
    first = NormalizedDocument(
        workspace_id="workspace",
        source_id=Source(
            workspace_id="workspace",
            name="policy",
            config=SourceConfig(source_type=SourceType.PDF),
        ).source_id,
        source_type=SourceType.PDF,
        content="First sentence. Target sentence. Last sentence.",
        page_number=4,
    )
    second = first.model_copy(
        update={
            "document_id": uuid4(),
            "content": "Another document sentence.",
        }
    )

    nodes = build_sentence_window_documents([first, second], radius=1)

    target = nodes[1]
    metadata = target.metadata["sentence_window"]
    assert target.content == "Target sentence."
    assert metadata["window_text"] == "First sentence. Target sentence. Last sentence."
    assert metadata["parent_document_id"] == str(first.document_id)
    assert target.page_number == 4
    assert "Another document sentence." not in str(metadata["window_text"])


@pytest.mark.asyncio
async def test_retriever_collapses_identical_expanded_windows(tmp_path: Path) -> None:
    source_id = uuid4()
    document = NormalizedDocument(
        workspace_id="workspace",
        source_id=source_id,
        source_type=SourceType.PDF,
        content="First sentence. Second sentence. Third sentence.",
        page_number=1,
    )
    nodes = build_sentence_window_documents([document], radius=2)
    vectors = np.asarray([[1.0, 0.0, 0.0]] * len(nodes), dtype=np.float32)
    store = FaissVectorStore(tmp_path)
    generation = await store.prepare_bundle(
        "workspace",
        {VectorIndexKind.SENTENCE_WINDOW: VectorIndexPayload(nodes, vectors)},
        model_name=ParagraphComparisonEmbeddings.model_name,
        dimension=ParagraphComparisonEmbeddings.dimension,
    )
    await store.activate("workspace", generation.generation_id)

    evidence = await SentenceWindowRetriever(ParagraphComparisonEmbeddings(), store).retrieve(
        "workspace",
        "sentence",
        top_k=3,
        source_ids=frozenset(),
        min_similarity=0.7,
    )

    assert len(evidence) == 1
    assert evidence[0].content == "First sentence. Second sentence. Third sentence."


@pytest.mark.asyncio
async def test_faiss_activates_and_searches_both_representations(tmp_path: Path) -> None:
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path)
    source = Source(
        workspace_id="workspace",
        name="retention policy",
        config=SourceConfig(source_type=SourceType.PDF),
    )
    await repository.create(source)
    await repository.transition("workspace", source.source_id, SourceStatus.INDEXING)
    source = await repository.transition("workspace", source.source_id, SourceStatus.READY)
    await storage.save_source(source)
    document = NormalizedDocument(
        workspace_id="workspace",
        source_id=source.source_id,
        source_type=SourceType.PDF,
        title=source.name,
        content=(
            "Records are stored securely. The retention period is seven years. "
            "Records are then destroyed."
        ),
        page_number=4,
    )
    await storage.save_documents("workspace", source.source_id, [document])
    embeddings = ParagraphComparisonEmbeddings()
    store = FaissVectorStore(tmp_path)
    metadata = await VectorIndexingService(
        repository, storage, embeddings, store, sentence_window_radius=1
    ).rebuild("workspace", source.source_id)

    assert metadata.generation_id
    vector_matches = await store.search(
        "workspace",
        await embeddings.embed_query("retention"),
        top_k=1,
        source_ids=frozenset(),
        model_name=embeddings.model_name,
        dimension=embeddings.dimension,
    )
    sentence_matches = await store.search(
        "workspace",
        await embeddings.embed_query("retention"),
        top_k=1,
        source_ids=frozenset(),
        model_name=embeddings.model_name,
        dimension=embeddings.dimension,
        index_kind=VectorIndexKind.SENTENCE_WINDOW,
    )
    assert vector_matches[0].document.document_id == document.document_id
    assert sentence_matches[0].document.content == "The retention period is seven years."


@pytest.mark.asyncio
async def test_curated_benchmark_reports_sentence_window_quality_over_baseline(
    tmp_path: Path,
) -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "sentence_window_benchmark.json"
    fixtures = TypeAdapter(list[BenchmarkFixture]).validate_json(fixture_path.read_bytes())
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path)
    sources: list[Source] = []
    for fixture in fixtures:
        source = Source(
            workspace_id="workspace",
            name=f"{fixture.key} policy",
            config=SourceConfig(source_type=SourceType.PDF),
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
                    source_type=SourceType.PDF,
                    title=source.name,
                    content=f"{fixture.before} {fixture.target} {fixture.after}",
                    page_number=fixture.page_number,
                )
            ],
        )
        sources.append(source)

    embeddings = CuratedBenchmarkEmbeddings(fixtures)
    store = FaissVectorStore(tmp_path)
    await VectorIndexingService(
        repository, storage, embeddings, store, sentence_window_radius=1
    ).rebuild("workspace", sources[0].source_id)
    service = QueryService(
        repository,
        VectorRetriever(embeddings, store),
        SentenceWindowRetriever(embeddings, store),
        ExtractiveGenerator(),
        default_top_k=3,
        max_top_k=20,
        min_similarity=0.7,
    )
    baseline_runs: list[RetrievalBenchmarkRun] = []
    window_runs: list[RetrievalBenchmarkRun] = []
    for fixture, source in zip(fixtures, sources, strict=True):
        case = RetrievalBenchmarkCase(
            question=fixture.question,
            expected_source_id=source.source_id,
            expected_locator=f"page {fixture.page_number}",
            required_context=fixture.after,
        )
        baseline_started = perf_counter()
        baseline = await service.query(
            QueryRequest(workspace_id="workspace", question=fixture.question)
        )
        baseline_runs.append(
            RetrievalBenchmarkRun(
                case=case,
                evidence=tuple(baseline.evidence),
                citations=tuple(baseline.citations),
                latency_seconds=perf_counter() - baseline_started,
            )
        )
        window_started = perf_counter()
        window = await service.query(
            QueryRequest(
                workspace_id="workspace",
                question=fixture.question,
                retrieval_mode=RetrievalMode.SENTENCE_WINDOW,
            )
        )
        window_runs.append(
            RetrievalBenchmarkRun(
                case=case,
                evidence=tuple(window.evidence),
                citations=tuple(window.citations),
                latency_seconds=perf_counter() - window_started,
            )
        )

    baseline_metrics = score_retrieval_runs(baseline_runs)
    window_metrics = score_retrieval_runs(window_runs)

    assert baseline_metrics.case_count == 3
    assert baseline_metrics.hit_rate_at_k == 0
    assert baseline_metrics.mean_reciprocal_rank == 0
    assert window_metrics.case_count == 3
    assert window_metrics.hit_rate_at_k == 1
    assert window_metrics.mean_reciprocal_rank == 1
    assert window_metrics.citation_accuracy == 1
    assert window_metrics.context_expansion_rate == 1
    assert window_metrics.hit_rate_at_k > baseline_metrics.hit_rate_at_k
    assert window_metrics.mean_reciprocal_rank > baseline_metrics.mean_reciprocal_rank
    assert baseline_metrics.mean_latency_seconds >= 0
    assert window_metrics.mean_latency_seconds >= 0


def test_retrieval_metrics_reject_empty_runs() -> None:
    with pytest.raises(ValueError, match="At least one"):
        score_retrieval_runs([])


def test_query_request_defaults_to_vector_mode() -> None:
    request = QueryRequest(workspace_id="workspace", question="What is the policy?")

    assert request.retrieval_mode is RetrievalMode.VECTOR

"""Phase 10 cross-encoder and diversity-aware re-ranking tests."""

from collections.abc import Sequence
from pathlib import Path
from time import perf_counter
from typing import Any, cast
from uuid import UUID, uuid4

import numpy as np
import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from app.citations.builder import build_citations
from app.core.exceptions import RerankingError
from app.evaluation.reranking_comparison import (
    RerankingBenchmarkCase,
    RerankingBenchmarkRun,
    score_reranking_runs,
)
from app.generation.extractive import ExtractiveGenerator
from app.models import Evidence, FusionStrategy, QueryRequest, RetrievalMode, SourceType
from app.repositories import InMemorySourceRepository
from app.reranking.service import (
    HuggingFaceCrossEncoderReranker,
    RerankerScores,
    RerankingService,
)
from app.retrieval.fusion import FusionRetriever
from app.retrieval.query_service import QueryService


class RerankingFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    question: str


class FakeCrossEncoder:
    """Record model inputs and return configured scores."""

    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.calls: list[tuple[list[tuple[str, str]], int, bool]] = []

    def predict(
        self,
        sentences: list[tuple[str, str]],
        *,
        batch_size: int,
        show_progress_bar: bool,
    ) -> Any:
        self.calls.append((sentences, batch_size, show_progress_bar))
        return self.scores


class FixedReranker:
    """Return scores derived from candidate content for deterministic tests."""

    model_name = "fixed-reranker"

    async def score(self, question: str, evidence: Sequence[Evidence]) -> RerankerScores:
        del question
        scores = tuple(float(item.content.split("grade:")[1]) for item in evidence)
        return RerankerScores(scores, self.model_name, 0.001)


def _evidence(content: str, *, source_id: UUID | None = None) -> Evidence:
    identifier = source_id if source_id is not None else uuid4()
    return Evidence(
        retriever="fusion",
        content=content,
        source_id=identifier,
        source_type=SourceType.PDF,
        page_number=2,
        raw_score=0.01,
        normalized_score=0.5,
        metadata={"fusion_contributions": [], "title": "policy"},
    )


def test_query_request_rejects_reranking_without_fusion() -> None:
    with pytest.raises(ValidationError, match="Fusion options"):
        QueryRequest(
            workspace_id="workspace",
            question="question",
            retrieval_mode=RetrievalMode.VECTOR,
            rerank=True,
        )


@pytest.mark.asyncio
async def test_cross_encoder_loads_lazily_once_and_batches_pairs() -> None:
    model = FakeCrossEncoder([0.2, 0.8])
    factory_calls: list[tuple[str, int, str | None]] = []

    def factory(name: str, max_length: int, device: str | None) -> FakeCrossEncoder:
        factory_calls.append((name, max_length, device))
        return model

    reranker = HuggingFaceCrossEncoderReranker(
        model_name="test-model",
        batch_size=8,
        max_length=256,
        device="auto",
        model_factory=factory,
    )
    assert factory_calls == []
    evidence = [_evidence("first"), _evidence("second")]

    first = await reranker.score("question", evidence)
    await reranker.score("question", evidence)

    assert first.scores == (0.2, 0.8)
    assert factory_calls == [("test-model", 256, None)]
    assert model.calls[0] == (
        [("question", "first"), ("question", "second")],
        8,
        False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("scores", [[0.1], [np.nan, 0.2], [np.inf, 0.2]])
async def test_cross_encoder_rejects_invalid_scores(scores: list[float]) -> None:
    reranker = HuggingFaceCrossEncoderReranker(
        model_name="test",
        batch_size=2,
        max_length=128,
        device="cpu",
        model_factory=lambda *_: FakeCrossEncoder(scores),
    )

    with pytest.raises(RerankingError):
        await reranker.score("question", [_evidence("a"), _evidence("b")])


@pytest.mark.asyncio
async def test_cross_encoder_wraps_model_loading_failure() -> None:
    def broken_factory(*_: object) -> FakeCrossEncoder:
        raise OSError("model unavailable")

    reranker = HuggingFaceCrossEncoderReranker(
        model_name="missing",
        batch_size=2,
        max_length=128,
        device="cpu",
        model_factory=broken_factory,
    )

    with pytest.raises(RerankingError, match="loading"):
        await reranker.score("question", [_evidence("candidate")])


@pytest.mark.asyncio
async def test_reranking_orders_scores_and_applies_diversity_then_fill() -> None:
    first_source, second_source = uuid4(), uuid4()
    candidates = [
        _evidence("first grade:3", source_id=first_source),
        _evidence("second grade:2", source_id=first_source),
        _evidence("third grade:1", source_id=second_source),
        _evidence("fourth grade:0", source_id=first_source),
    ]
    service = RerankingService(FixedReranker(), max_per_source=1)

    result = await service.rerank("question", candidates, top_k=3)

    assert [item.content for item in result] == [
        "first grade:3",
        "third grade:1",
        "second grade:2",
    ]
    assert [item.evidence_id for item in result] == [
        candidates[0].evidence_id,
        candidates[2].evidence_id,
        candidates[1].evidence_id,
    ]
    assert all(item.retriever == "rerank" for item in result)
    assert result[0].metadata["reranking"]["fused_score"] == 0.01
    assert result[0].metadata["reranking"]["model_latency_ms"] == 1


@pytest.mark.asyncio
async def test_equal_scores_are_normalized_and_tied_by_fused_rank() -> None:
    candidates = [_evidence("a grade:1"), _evidence("b grade:1")]

    result = await RerankingService(FixedReranker(), max_per_source=2).rerank(
        "question", candidates, top_k=2
    )

    assert [item.evidence_id for item in result] == [item.evidence_id for item in candidates]
    assert [item.normalized_score for item in result] == [1, 1]


@pytest.mark.asyncio
async def test_query_service_requests_reranking_candidate_pool_then_returns_top_k() -> None:
    candidates = [_evidence(f"candidate-{index} grade:{index}") for index in range(5)]

    class RecordingFusion:
        def __init__(self) -> None:
            self.top_k: int | None = None

        async def retrieve(
            self,
            workspace_id: str,
            query: str,
            *,
            top_k: int,
            source_ids: frozenset[UUID],
            modes: tuple[RetrievalMode, ...],
            strategy: FusionStrategy,
            min_similarity: float | None = None,
        ) -> list[Evidence]:
            del workspace_id, query, source_ids, modes, strategy, min_similarity
            self.top_k = top_k
            return candidates[:top_k]

    fusion = RecordingFusion()
    service = QueryService(
        InMemorySourceRepository(),
        cast(Any, object()),
        cast(Any, object()),
        ExtractiveGenerator(),
        default_top_k=2,
        max_top_k=20,
        min_similarity=0.7,
        fusion_retriever=cast(FusionRetriever, fusion),
        reranking_service=RerankingService(FixedReranker(), max_per_source=2),
        reranking_candidate_pool_size=4,
    )

    response = await service.query(
        QueryRequest(
            workspace_id="workspace",
            question="question",
            top_k=2,
            retrieval_mode=RetrievalMode.FUSION,
            fusion_retrievers=[RetrievalMode.VECTOR, RetrievalMode.LEXICAL],
            rerank=True,
        )
    )

    assert fusion.top_k == 4
    assert len(response.evidence) == 2
    assert [item.content for item in response.evidence] == [
        "candidate-3 grade:3",
        "candidate-2 grade:2",
    ]


@pytest.mark.asyncio
async def test_eight_case_benchmark_improves_mrr_and_ndcg_with_citations() -> None:
    fixtures = TypeAdapter(list[RerankingFixture]).validate_json(
        (Path(__file__).parent / "fixtures" / "reranking_benchmark.json").read_bytes()
    )
    assert len(fixtures) >= 8
    service = RerankingService(FixedReranker(), max_per_source=2)
    fused_runs: list[RerankingBenchmarkRun] = []
    reranked_runs: list[RerankingBenchmarkRun] = []
    for fixture in fixtures:
        irrelevant, secondary, primary = uuid4(), uuid4(), uuid4()
        candidates = [
            _evidence(f"{fixture.key} distractor grade:0", source_id=irrelevant),
            _evidence(f"{fixture.key} supporting grade:2", source_id=secondary),
            _evidence(f"{fixture.key} primary grade:3", source_id=primary),
        ]
        case = RerankingBenchmarkCase(
            question=fixture.question,
            relevance_by_evidence={
                candidates[0].evidence_id: 0,
                candidates[1].evidence_id: 2,
                candidates[2].evidence_id: 3,
            },
        )
        fused_runs.append(
            RerankingBenchmarkRun(
                case=case,
                evidence=tuple(candidates),
                citations=tuple(build_citations(candidates)),
                latency_seconds=0,
            )
        )
        started = perf_counter()
        reranked = await service.rerank(fixture.question, candidates, top_k=3)
        reranked_runs.append(
            RerankingBenchmarkRun(
                case=case,
                evidence=tuple(reranked),
                citations=tuple(build_citations(reranked)),
                latency_seconds=perf_counter() - started,
            )
        )
    fused_metrics = score_reranking_runs(fused_runs)
    reranked_metrics = score_reranking_runs(reranked_runs)

    assert reranked_metrics.mean_reciprocal_rank == 1
    assert reranked_metrics.ndcg_at_k == 1
    assert reranked_metrics.citation_accuracy == 1
    assert reranked_metrics.mean_reciprocal_rank > fused_metrics.mean_reciprocal_rank
    assert reranked_metrics.ndcg_at_k > fused_metrics.ndcg_at_k
    assert reranked_metrics.mean_source_coverage == 1
    assert reranked_metrics.mean_latency_seconds >= 0


def test_metrics_bound_ndcg_and_fail_citation_accuracy_when_relevant_evidence_is_missing() -> None:
    source_id = uuid4()
    first = _evidence("first grade:3", source_id=source_id)
    second = _evidence("second grade:2", source_id=source_id)
    case = RerankingBenchmarkCase(
        question="question",
        relevance_by_evidence={first.evidence_id: 3, second.evidence_id: 2},
    )
    duplicate_source_metrics = score_reranking_runs(
        [
            RerankingBenchmarkRun(
                case=case,
                evidence=(first, second),
                citations=tuple(build_citations([first, second])),
                latency_seconds=0,
            )
        ]
    )
    missing_metrics = score_reranking_runs(
        [
            RerankingBenchmarkRun(
                case=case,
                evidence=(),
                citations=(),
                latency_seconds=0,
            )
        ]
    )

    assert 0 <= duplicate_source_metrics.ndcg_at_k <= 1
    assert missing_metrics.citation_accuracy == 0

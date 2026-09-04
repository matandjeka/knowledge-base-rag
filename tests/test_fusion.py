"""Phase 9 reciprocal-rank fusion and orchestration tests."""

import asyncio
from pathlib import Path
from time import perf_counter
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from app.citations.builder import build_citations
from app.core.exceptions import IndexNotFoundError
from app.evaluation.retrieval_comparison import (
    RetrievalBenchmarkCase,
    RetrievalBenchmarkRun,
    score_retrieval_runs,
)
from app.generation.extractive import ExtractiveGenerator
from app.models import (
    Evidence,
    FusionStrategy,
    GraphPathSupport,
    GraphSupport,
    QueryRequest,
    RetrievalMode,
    Source,
    SourceConfig,
    SourceType,
)
from app.repositories import InMemorySourceRepository
from app.retrieval.fusion import FusionRetriever, fuse_ranked_results
from app.retrieval.query_service import QueryService


class FusionFixture(BaseModel):
    """One committed complementary-ranking fusion case."""

    model_config = ConfigDict(extra="forbid")

    key: str
    question: str


def _evidence(
    retriever: RetrievalMode,
    document_id: UUID,
    *,
    content: str | None = None,
    source_id: UUID | None = None,
    rank_score: float = 0.9,
) -> Evidence:
    metadata: dict[str, object] = {
        "title": "policy",
        "document_id": str(document_id),
    }
    if retriever is RetrievalMode.SENTENCE_WINDOW:
        metadata["parent_document_id"] = str(document_id)
    return Evidence(
        retriever=retriever.value,
        content=content or f"Evidence for {document_id}",
        source_id=source_id or uuid4(),
        source_type=SourceType.PDF,
        page_number=2,
        raw_score=rank_score,
        normalized_score=rank_score,
        metadata=metadata,
    )


class StubRetriever:
    """Return configured evidence while recording retrieval arguments."""

    def __init__(self, evidence: list[Evidence], *, fail: bool = False) -> None:
        self.evidence = evidence
        self.fail = fail
        self.calls: list[tuple[int, float, frozenset[UUID]]] = []

    async def retrieve(
        self,
        workspace_id: str,
        query: str,
        *,
        top_k: int,
        source_ids: frozenset[UUID],
        min_similarity: float,
    ) -> list[Evidence]:
        del workspace_id, query
        self.calls.append((top_k, min_similarity, source_ids))
        await asyncio.sleep(0)
        if self.fail:
            raise IndexNotFoundError("requested index is missing")
        return self.evidence[:top_k]


def _weights() -> dict[RetrievalMode, float]:
    return {
        RetrievalMode.VECTOR: 0.4,
        RetrievalMode.SENTENCE_WINDOW: 0.25,
        RetrievalMode.GRAPH: 0.25,
        RetrievalMode.LEXICAL: 0.1,
    }


def test_query_request_validates_fusion_options() -> None:
    request = QueryRequest(
        workspace_id="workspace",
        question="What is the policy?",
        retrieval_mode=RetrievalMode.FUSION,
    )
    assert request.fusion_retrievers is None

    with pytest.raises(ValidationError, match="at least two"):
        QueryRequest(
            workspace_id="workspace",
            question="question",
            retrieval_mode=RetrievalMode.FUSION,
            fusion_retrievers=[RetrievalMode.VECTOR],
        )
    with pytest.raises(ValidationError, match="distinct"):
        QueryRequest(
            workspace_id="workspace",
            question="question",
            retrieval_mode=RetrievalMode.FUSION,
            fusion_retrievers=[RetrievalMode.VECTOR, RetrievalMode.VECTOR],
        )
    with pytest.raises(ValidationError, match="require"):
        QueryRequest(
            workspace_id="workspace",
            question="question",
            fusion_strategy=FusionStrategy.RRF,
        )


def test_rrf_collapses_document_duplicates_and_prefers_sentence_window() -> None:
    document_id = uuid4()
    source_id = uuid4()
    vector = _evidence(RetrievalMode.VECTOR, document_id, source_id=source_id)
    lexical = _evidence(RetrievalMode.LEXICAL, document_id, source_id=source_id)
    window = _evidence(
        RetrievalMode.SENTENCE_WINDOW,
        document_id,
        source_id=source_id,
        content="Focused sentence and its neighbors.",
    )

    first = fuse_ranked_results(
        "workspace",
        {
            RetrievalMode.VECTOR: [vector, vector.model_copy(update={"evidence_id": uuid4()})],
            RetrievalMode.LEXICAL: [lexical],
            RetrievalMode.SENTENCE_WINDOW: [window],
        },
        top_k=5,
        strategy=FusionStrategy.RRF,
        rrf_k=60,
        weights=_weights(),
    )
    second = fuse_ranked_results(
        "workspace",
        {
            RetrievalMode.VECTOR: [vector, vector.model_copy(update={"evidence_id": uuid4()})],
            RetrievalMode.LEXICAL: [lexical],
            RetrievalMode.SENTENCE_WINDOW: [window],
        },
        top_k=5,
        strategy=FusionStrategy.RRF,
        rrf_k=60,
        weights=_weights(),
    )

    assert len(first) == 1
    assert first[0].content == "Focused sentence and its neighbors."
    assert first[0].evidence_id == second[0].evidence_id
    assert first[0].raw_score == pytest.approx(3 / 61)
    assert first[0].normalized_score == 1
    assert set(first[0].metadata["contributing_retrievers"]) == {
        "vector",
        "sentence_window",
        "lexical",
    }


def test_weighted_rrf_normalizes_active_weights_and_orders_deterministically() -> None:
    shared_id = uuid4()
    lexical_only_id = uuid4()
    shared_source = uuid4()
    results = {
        RetrievalMode.VECTOR: [_evidence(RetrievalMode.VECTOR, shared_id, source_id=shared_source)],
        RetrievalMode.LEXICAL: [
            _evidence(RetrievalMode.LEXICAL, lexical_only_id),
            _evidence(RetrievalMode.LEXICAL, shared_id, source_id=shared_source),
        ],
    }

    fused = fuse_ranked_results(
        "workspace",
        results,
        top_k=2,
        strategy=FusionStrategy.WEIGHTED_RRF,
        rrf_k=60,
        weights=_weights(),
    )

    assert fused[0].metadata["canonical_evidence_key"].endswith(str(shared_id))
    contributions = fused[0].metadata["fusion_contributions"]
    contribution_weights = {item["retriever"]: item["weight"] for item in contributions}
    assert contribution_weights == pytest.approx({"lexical": 0.2, "vector": 0.8})
    assert fused[0].raw_score == pytest.approx(0.8 / 61 + 0.2 / 62)


@pytest.mark.asyncio
async def test_fusion_retriever_expands_candidate_depth_filters_and_uses_thresholds() -> None:
    source_id = uuid4()
    vector = StubRetriever([_evidence(RetrievalMode.VECTOR, uuid4())])
    lexical = StubRetriever([_evidence(RetrievalMode.LEXICAL, uuid4())])
    fusion = FusionRetriever(
        {RetrievalMode.VECTOR: vector, RetrievalMode.LEXICAL: lexical},
        max_top_k=5,
        candidate_multiplier=3,
        rrf_k=60,
        weights=_weights(),
        min_similarity=0.7,
        lexical_min_score=0.2,
    )

    evidence = await fusion.retrieve(
        "workspace",
        "question",
        top_k=2,
        source_ids=frozenset({source_id}),
        modes=(RetrievalMode.VECTOR, RetrievalMode.LEXICAL),
        strategy=FusionStrategy.RRF,
    )

    assert len(evidence) == 2
    assert vector.calls == [(5, 0.7, frozenset({source_id}))]
    assert lexical.calls == [(5, 0.2, frozenset({source_id}))]


@pytest.mark.asyncio
async def test_fusion_retriever_strictly_propagates_requested_failure() -> None:
    fusion = FusionRetriever(
        {
            RetrievalMode.VECTOR: StubRetriever([]),
            RetrievalMode.GRAPH: StubRetriever([], fail=True),
        },
        max_top_k=20,
        candidate_multiplier=3,
        rrf_k=60,
        weights=_weights(),
        min_similarity=0.7,
        lexical_min_score=0,
    )

    with pytest.raises(IndexNotFoundError, match="missing"):
        await fusion.retrieve(
            "workspace",
            "question",
            top_k=5,
            source_ids=frozenset(),
            modes=(RetrievalMode.VECTOR, RetrievalMode.GRAPH),
            strategy=FusionStrategy.RRF,
        )


def test_fused_graph_evidence_preserves_edge_citations() -> None:
    relationship_id = uuid4()
    source_id = uuid4()
    support = GraphSupport(
        document_id=uuid4(),
        source_id=source_id,
        source_type=SourceType.PDF,
        text="Policy HR-402 governs Project Atlas.",
        start=0,
        end=37,
        title="policy",
        page_number=8,
    )
    graph = Evidence(
        retriever="graph",
        content="Project Atlas -> governed by -> HR-402",
        source_id=source_id,
        source_type=SourceType.PDF,
        page_number=8,
        raw_score=1,
        normalized_score=1,
        metadata={
            "graph_path": [{"relationship_id": str(relationship_id)}],
            "citation_supports": [
                GraphPathSupport(
                    relationship_id=relationship_id,
                    support=support,
                ).model_dump(mode="json")
            ],
        },
    )

    fused = fuse_ranked_results(
        "workspace",
        {RetrievalMode.GRAPH: [graph], RetrievalMode.VECTOR: []},
        top_k=1,
        strategy=FusionStrategy.RRF,
        rrf_k=60,
        weights=_weights(),
    )
    citations = build_citations(fused)

    assert fused[0].retriever == "fusion"
    assert citations[0].locator == "page 8"
    assert fused[0].metadata["edge_citation_ids"] == {str(relationship_id): ["S1"]}


@pytest.mark.asyncio
async def test_query_service_returns_fused_response() -> None:
    repository = InMemorySourceRepository()
    source = Source(
        workspace_id="workspace",
        name="policy",
        config=SourceConfig(source_type=SourceType.PDF),
    )
    await repository.create(source)
    document_id = uuid4()
    vector = StubRetriever(
        [_evidence(RetrievalMode.VECTOR, document_id, source_id=source.source_id)]
    )
    lexical = StubRetriever(
        [_evidence(RetrievalMode.LEXICAL, document_id, source_id=source.source_id)]
    )
    fusion = FusionRetriever(
        {RetrievalMode.VECTOR: vector, RetrievalMode.LEXICAL: lexical},
        max_top_k=20,
        candidate_multiplier=3,
        rrf_k=60,
        weights=_weights(),
        min_similarity=0.7,
        lexical_min_score=0,
    )
    service = QueryService(
        repository,
        vector,
        vector,
        ExtractiveGenerator(),
        default_top_k=5,
        max_top_k=20,
        min_similarity=0.7,
        fusion_retriever=fusion,
    )

    response = await service.query(
        QueryRequest(
            workspace_id="workspace",
            question="What is the policy?",
            retrieval_mode=RetrievalMode.FUSION,
            fusion_retrievers=[RetrievalMode.VECTOR, RetrievalMode.LEXICAL],
        )
    )

    assert len(response.evidence) == 1
    assert response.evidence[0].retriever == "fusion"
    assert response.citations[0].source_id == source.source_id


def test_six_case_benchmark_fusion_outperforms_individual_rankings() -> None:
    fixtures = TypeAdapter(list[FusionFixture]).validate_json(
        (Path(__file__).parent / "fixtures" / "fusion_benchmark.json").read_bytes()
    )
    assert len(fixtures) >= 6
    vector_runs: list[RetrievalBenchmarkRun] = []
    lexical_runs: list[RetrievalBenchmarkRun] = []
    fusion_runs: list[RetrievalBenchmarkRun] = []
    for fixture in fixtures:
        expected_document = uuid5(NAMESPACE_URL, f"fusion:{fixture.key}:expected")
        expected_source = uuid5(NAMESPACE_URL, f"fusion:{fixture.key}:source")
        expected_vector = _evidence(
            RetrievalMode.VECTOR,
            expected_document,
            source_id=expected_source,
            content=f"Expected evidence for {fixture.key}.",
        )
        expected_lexical = _evidence(
            RetrievalMode.LEXICAL,
            expected_document,
            source_id=expected_source,
            content=f"Expected evidence for {fixture.key}.",
        )
        vector_candidates = [
            _evidence(
                RetrievalMode.VECTOR,
                uuid5(NAMESPACE_URL, f"fusion:{fixture.key}:vector-distractor"),
            ),
            expected_vector,
        ]
        lexical_candidates = [
            _evidence(
                RetrievalMode.LEXICAL,
                uuid5(NAMESPACE_URL, f"fusion:{fixture.key}:lexical-distractor"),
            ),
            expected_lexical,
        ]
        case = RetrievalBenchmarkCase(
            question=fixture.question,
            expected_source_id=expected_source,
            expected_locator="page 2",
            required_context=fixture.key,
        )
        vector_runs.append(
            RetrievalBenchmarkRun(
                case=case,
                evidence=tuple(vector_candidates[:1]),
                citations=tuple(build_citations(vector_candidates[:1])),
                latency_seconds=0,
            )
        )
        lexical_runs.append(
            RetrievalBenchmarkRun(
                case=case,
                evidence=tuple(lexical_candidates[:1]),
                citations=tuple(build_citations(lexical_candidates[:1])),
                latency_seconds=0,
            )
        )
        started = perf_counter()
        fused = fuse_ranked_results(
            "workspace",
            {
                RetrievalMode.VECTOR: vector_candidates,
                RetrievalMode.LEXICAL: lexical_candidates,
            },
            top_k=1,
            strategy=FusionStrategy.RRF,
            rrf_k=60,
            weights=_weights(),
        )
        fusion_runs.append(
            RetrievalBenchmarkRun(
                case=case,
                evidence=tuple(fused),
                citations=tuple(build_citations(fused)),
                latency_seconds=perf_counter() - started,
            )
        )

    vector_metrics = score_retrieval_runs(vector_runs)
    lexical_metrics = score_retrieval_runs(lexical_runs)
    fusion_metrics = score_retrieval_runs(fusion_runs)

    assert fusion_metrics.hit_rate_at_k == 1
    assert fusion_metrics.mean_reciprocal_rank == 1
    assert fusion_metrics.citation_accuracy == 1
    assert fusion_metrics.hit_rate_at_k > vector_metrics.hit_rate_at_k
    assert fusion_metrics.hit_rate_at_k > lexical_metrics.hit_rate_at_k
    assert fusion_metrics.mean_reciprocal_rank > vector_metrics.mean_reciprocal_rank
    assert fusion_metrics.mean_reciprocal_rank > lexical_metrics.mean_reciprocal_rank
    assert fusion_metrics.mean_latency_seconds >= 0

"""Phase 12 deterministic query routing and evaluation tests."""

import json
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter

from app.core.exceptions import GraphIndexNotFoundError, RoutingError
from app.evaluation.routing_comparison import (
    RoutingBenchmarkObservation,
    score_routing_observations,
)
from app.generation.extractive import ExtractiveGenerator
from app.models import (
    Evidence,
    QueryRequest,
    RetrievalMode,
    RoutingIntent,
    RoutingKind,
    Source,
    SourceConfig,
    SourceStatus,
    SourceType,
)
from app.repositories import InMemorySourceRepository
from app.retrieval.fusion import DEFAULT_FUSION_RETRIEVERS, FusionRetriever
from app.retrieval.query_service import QueryService
from app.routing import RuleBasedQueryRouter, normalize_query


class RoutingFixture(BaseModel):
    """One expected deterministic routing decision."""

    model_config = ConfigDict(extra="forbid")

    key: str
    question: str
    expected_intent: RoutingIntent
    expected_retrievers: list[RetrievalMode]


class EmptyRetriever:
    """Return no evidence while recording source filters."""

    def __init__(self) -> None:
        self.calls: list[frozenset[UUID]] = []

    async def retrieve(
        self,
        workspace_id: str,
        question: str,
        *,
        top_k: int,
        source_ids: frozenset[UUID],
        min_similarity: float = 0.0,
    ) -> list[Evidence]:
        del workspace_id, question, top_k, min_similarity
        self.calls.append(source_ids)
        return []


class RecordingFusion:
    """Record routed fusion plans and optionally simulate a missing graph index."""

    def __init__(self, *, graph_missing: bool = False) -> None:
        self.graph_missing = graph_missing
        self.modes: list[tuple[RetrievalMode, ...]] = []

    async def retrieve(self, *args: Any, **kwargs: Any) -> list[Evidence]:
        del args
        modes = cast(tuple[RetrievalMode, ...], kwargs["modes"])
        self.modes.append(modes)
        if self.graph_missing and RetrievalMode.GRAPH in modes:
            raise GraphIndexNotFoundError("No graph index")
        return []


def _database(name: str = "database") -> Source:
    return Source(
        workspace_id="workspace",
        name=name,
        status=SourceStatus.READY,
        config=SourceConfig(source_type=SourceType.DATABASE),
    )


def _router() -> RuleBasedQueryRouter:
    return RuleBasedQueryRouter(confidence_threshold=0.70, winning_margin=0.15)


def _available() -> frozenset[RetrievalMode]:
    return frozenset(
        {
            RetrievalMode.VECTOR,
            RetrievalMode.SENTENCE_WINDOW,
            RetrievalMode.GRAPH,
            RetrievalMode.LEXICAL,
            RetrievalMode.SQL,
        }
    )


def test_query_normalization_preserves_identifier_case_and_compacts_space() -> None:
    assert normalize_query("  What   does HR-402 say?  ") == "What does HR-402 say?"


def test_committed_routing_benchmark_meets_plan_accuracy_gate() -> None:
    fixtures = TypeAdapter(list[RoutingFixture]).validate_python(
        json.loads((Path(__file__).parent / "fixtures" / "routing_benchmark.json").read_text())
    )
    database = _database()
    decisions = [_router().route(case.question, [database], [], _available()) for case in fixtures]

    correct = [
        decision.intent is case.expected_intent
        and decision.selected_retrievers == case.expected_retrievers
        for case, decision in zip(fixtures, decisions, strict=True)
    ]

    assert sum(correct) / len(correct) >= 0.90
    assert all(correct)
    assert all(decision.matched_rules for decision in decisions if decision.confidence > 0)


def test_mixed_intent_falls_back_and_missing_capability_uses_default_fusion() -> None:
    database = _database()
    mixed = _router().route("Who owns policy HR-402?", [database], [], _available())
    missing_graph = _router().route(
        "Who owns Project Atlas?",
        [database],
        [],
        _available() - {RetrievalMode.GRAPH},
    )

    assert mixed.intent is RoutingIntent.DEFAULT
    assert mixed.fallback_reason == "mixed_or_ambiguous_intent"
    assert mixed.selected_retrievers == list(DEFAULT_FUSION_RETRIEVERS)
    assert missing_graph.intent is RoutingIntent.DEFAULT
    assert missing_graph.fallback_reason == "retriever_unavailable:graph"


def test_sql_routing_selects_one_database_and_refuses_ambiguity() -> None:
    first = _database("first")
    second = _database("second")
    selected = _router().route(
        "Compare revenue by region", [first, second], [second.source_id], _available()
    )

    assert selected.intent is RoutingIntent.SQL
    assert selected.selected_source_ids == [second.source_id]
    with pytest.raises(RoutingError, match="selecting one database"):
        _router().route("Compare revenue by region", [first, second], [], _available())


def test_sql_intent_without_a_ready_database_falls_back_to_document_fusion() -> None:
    decision = _router().route("Compare revenue by region", [], [], _available())

    assert decision.intent is RoutingIntent.DEFAULT
    assert decision.selected_retrievers == list(DEFAULT_FUSION_RETRIEVERS)
    assert decision.fallback_reason == "no_ready_database_source"


@pytest.mark.asyncio
async def test_query_service_exposes_override_and_automatic_traces() -> None:
    repository = InMemorySourceRepository()
    empty = EmptyRetriever()
    fusion = RecordingFusion()
    service = QueryService(
        repository,
        empty,
        empty,
        ExtractiveGenerator(),
        default_top_k=5,
        max_top_k=20,
        min_similarity=0.7,
        lexical_retriever=empty,
        graph_retriever=empty,
        fusion_retriever=cast(FusionRetriever, fusion),
        query_router=_router(),
    )

    explicit = await service.query(
        QueryRequest(workspace_id="workspace", question="General question")
    )
    automatic = await service.query(
        QueryRequest(
            workspace_id="workspace",
            question="What does policy HR-402 say?",
            retrieval_mode=RetrievalMode.AUTO,
        )
    )

    assert explicit.routing_trace is not None
    assert explicit.routing_trace.kind is RoutingKind.OVERRIDE
    assert explicit.routing_trace.selected_retrievers == [RetrievalMode.VECTOR]
    assert automatic.routing_trace is not None
    assert automatic.routing_trace.kind is RoutingKind.AUTO
    assert automatic.routing_trace.intent is RoutingIntent.LEXICAL
    assert fusion.modes == [(RetrievalMode.LEXICAL, RetrievalMode.VECTOR)]


@pytest.mark.asyncio
async def test_missing_graph_index_falls_back_to_default_fusion() -> None:
    repository = InMemorySourceRepository()
    empty = EmptyRetriever()
    fusion = RecordingFusion(graph_missing=True)
    service = QueryService(
        repository,
        empty,
        empty,
        ExtractiveGenerator(),
        default_top_k=5,
        max_top_k=20,
        min_similarity=0.7,
        lexical_retriever=empty,
        graph_retriever=empty,
        fusion_retriever=cast(FusionRetriever, fusion),
        query_router=_router(),
    )

    response = await service.query(
        QueryRequest(
            workspace_id="workspace",
            question="Who owns Project Atlas?",
            retrieval_mode=RetrievalMode.AUTO,
        )
    )

    assert response.routing_trace is not None
    assert response.routing_trace.intent is RoutingIntent.DEFAULT
    assert response.routing_trace.fallback_reason == "graph_index_unavailable"
    assert fusion.modes == [
        (RetrievalMode.GRAPH, RetrievalMode.VECTOR),
        DEFAULT_FUSION_RETRIEVERS,
    ]


def test_routing_quality_gate_preserves_quality_and_improves_specialized_latency() -> None:
    observations = [
        RoutingBenchmarkObservation(
            expected_plan_matched=True,
            safe_sql_behavior=True if index < 3 else None,
            routed_hit=True,
            baseline_hit=True,
            routed_reciprocal_rank=1.0,
            baseline_reciprocal_rank=1.0,
            routed_citation_correct=True,
            baseline_citation_correct=True,
            routed_latency_seconds=0.01 if index < 9 else 0.02,
            baseline_latency_seconds=0.02,
            specialized=index < 9,
        )
        for index in range(12)
    ]

    metrics = score_routing_observations(observations)

    assert metrics.plan_accuracy >= 0.90
    assert metrics.safe_sql_accuracy == 1.0
    assert metrics.hit_rate_delta >= -0.02
    assert metrics.mean_reciprocal_rank_delta >= -0.02
    assert metrics.citation_accuracy_delta >= 0
    assert metrics.specialized_median_latency_improvement > 0


def test_routing_metrics_require_a_specialized_latency_observation() -> None:
    observation = RoutingBenchmarkObservation(
        expected_plan_matched=True,
        safe_sql_behavior=None,
        routed_hit=True,
        baseline_hit=True,
        routed_reciprocal_rank=1.0,
        baseline_reciprocal_rank=1.0,
        routed_citation_correct=True,
        baseline_citation_correct=True,
        routed_latency_seconds=0.01,
        baseline_latency_seconds=0.02,
        specialized=False,
    )

    with pytest.raises(ValueError, match="specialized routing observation"):
        score_routing_observations([observation])

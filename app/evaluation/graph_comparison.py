"""Deterministic Phase 7 multi-hop retrieval benchmark metrics."""

from dataclasses import dataclass
from statistics import fmean
from uuid import UUID

from app.models import Citation, Evidence


@dataclass(frozen=True, slots=True)
class GraphBenchmarkCase:
    """Expected anchor, relationship path, and source locators for one graph question."""

    question: str
    anchor_entity_id: UUID
    expected_relationship_ids: tuple[UUID, ...]
    expected_locators: frozenset[str]


@dataclass(frozen=True, slots=True)
class GraphBenchmarkRun:
    """Observed graph evidence, citations, and latency for one case."""

    case: GraphBenchmarkCase
    evidence: tuple[Evidence, ...]
    citations: tuple[Citation, ...]
    latency_seconds: float


@dataclass(frozen=True, slots=True)
class GraphRetrievalMetrics:
    """Aggregated multi-hop graph retrieval and provenance quality."""

    case_count: int
    path_hit_rate_at_k: float
    path_mean_reciprocal_rank: float
    entity_linking_accuracy: float
    edge_citation_coverage: float
    cross_source_citation_accuracy: float
    mean_latency_seconds: float


def score_graph_runs(runs: list[GraphBenchmarkRun]) -> GraphRetrievalMetrics:
    """Score expected paths, anchors, edge citations, locators, and latency."""
    if not runs:
        raise ValueError("At least one graph benchmark run is required")
    hits: list[float] = []
    reciprocal_ranks: list[float] = []
    linking: list[float] = []
    coverage: list[float] = []
    citations: list[float] = []
    for run in runs:
        if run.latency_seconds < 0:
            raise ValueError("Graph benchmark latency cannot be negative")
        expected = tuple(str(identifier) for identifier in run.case.expected_relationship_ids)
        rank = next(
            (
                position
                for position, evidence in enumerate(run.evidence, start=1)
                if _relationship_path(evidence) == expected
            ),
            None,
        )
        hits.append(float(rank is not None))
        reciprocal_ranks.append(0.0 if rank is None else 1.0 / rank)
        linking.append(
            float(
                any(
                    str(run.case.anchor_entity_id) in evidence.entity_ids
                    for evidence in run.evidence
                )
            )
        )
        matched = run.evidence[rank - 1] if rank is not None else None
        edge_citations = matched.metadata.get("edge_citation_ids", {}) if matched else {}
        coverage.append(
            sum(float(str(identifier) in edge_citations) for identifier in expected) / len(expected)
        )
        actual_locators = {
            citation.locator
            for citation in run.citations
            if matched is not None and citation.evidence_id == matched.evidence_id
        }
        citations.append(float(run.case.expected_locators <= actual_locators))
    return GraphRetrievalMetrics(
        case_count=len(runs),
        path_hit_rate_at_k=fmean(hits),
        path_mean_reciprocal_rank=fmean(reciprocal_ranks),
        entity_linking_accuracy=fmean(linking),
        edge_citation_coverage=fmean(coverage),
        cross_source_citation_accuracy=fmean(citations),
        mean_latency_seconds=fmean(run.latency_seconds for run in runs),
    )


def _relationship_path(evidence: Evidence) -> tuple[str, ...]:
    payload = evidence.metadata.get("graph_path")
    if not isinstance(payload, list):
        return ()
    return tuple(
        str(item.get("relationship_id"))
        for item in payload
        if isinstance(item, dict) and item.get("relationship_id") is not None
    )

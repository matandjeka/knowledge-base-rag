"""Graded relevance metrics for fused-only and re-ranked evidence."""

import math
from dataclasses import dataclass
from statistics import fmean
from uuid import UUID

from app.models import Citation, Evidence


@dataclass(frozen=True, slots=True)
class RerankingBenchmarkCase:
    """One question with evidence-level graded relevance labels."""

    question: str
    relevance_by_evidence: dict[UUID, int]


@dataclass(frozen=True, slots=True)
class RerankingBenchmarkRun:
    """One ranked result list with citations and measured latency."""

    case: RerankingBenchmarkCase
    evidence: tuple[Evidence, ...]
    citations: tuple[Citation, ...]
    latency_seconds: float


@dataclass(frozen=True, slots=True)
class RerankingMetrics:
    """Aggregate ranked relevance, provenance, diversity, and latency."""

    case_count: int
    mean_reciprocal_rank: float
    ndcg_at_k: float
    citation_accuracy: float
    mean_source_coverage: float
    mean_latency_seconds: float


def score_reranking_runs(runs: list[RerankingBenchmarkRun]) -> RerankingMetrics:
    """Score graded retrieval runs at their observed result depth."""
    if not runs:
        raise ValueError("At least one re-ranking benchmark run is required")
    reciprocal_ranks: list[float] = []
    ndcg_scores: list[float] = []
    citation_scores: list[float] = []
    source_coverage: list[float] = []
    for run in runs:
        if run.latency_seconds < 0:
            raise ValueError("Re-ranking benchmark latency cannot be negative")
        relevance = [
            run.case.relevance_by_evidence.get(item.evidence_id, 0) for item in run.evidence
        ]
        first_relevant = next(
            (rank for rank, grade in enumerate(relevance, start=1) if grade > 0), None
        )
        reciprocal_ranks.append(0 if first_relevant is None else 1 / first_relevant)
        ideal = sorted(run.case.relevance_by_evidence.values(), reverse=True)[: len(relevance)]
        ideal_dcg = _dcg(ideal)
        ndcg_scores.append(0 if ideal_dcg == 0 else _dcg(relevance) / ideal_dcg)
        relevant_evidence = {
            item.evidence_id
            for item in run.evidence
            if run.case.relevance_by_evidence.get(item.evidence_id, 0) > 0
        }
        cited_evidence = {citation.evidence_id for citation in run.citations}
        has_expected_relevance = any(grade > 0 for grade in run.case.relevance_by_evidence.values())
        citation_scores.append(
            float(
                bool(relevant_evidence)
                and relevant_evidence <= cited_evidence
                and has_expected_relevance
            )
        )
        source_coverage.append(
            0
            if not run.evidence
            else len({item.source_id for item in run.evidence}) / len(run.evidence)
        )
    return RerankingMetrics(
        case_count=len(runs),
        mean_reciprocal_rank=fmean(reciprocal_ranks),
        ndcg_at_k=fmean(ndcg_scores),
        citation_accuracy=fmean(citation_scores),
        mean_source_coverage=fmean(source_coverage),
        mean_latency_seconds=fmean(run.latency_seconds for run in runs),
    )


def _dcg(relevance: list[int]) -> float:
    return sum(float(2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(relevance, 1))

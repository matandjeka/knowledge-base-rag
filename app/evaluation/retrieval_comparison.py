"""Focused deterministic metrics for comparing retrieval strategies."""

from dataclasses import dataclass
from statistics import fmean
from uuid import UUID

from app.models import Citation, Evidence


@dataclass(frozen=True, slots=True)
class RetrievalBenchmarkCase:
    """Expected source, locator, and neighboring context for one question."""

    question: str
    expected_source_id: UUID
    expected_locator: str
    required_context: str


@dataclass(frozen=True, slots=True)
class RetrievalBenchmarkRun:
    """Observed results and latency for one benchmark case and strategy."""

    case: RetrievalBenchmarkCase
    evidence: tuple[Evidence, ...]
    citations: tuple[Citation, ...]
    latency_seconds: float


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    """Aggregated quality and latency measurements for one retrieval strategy."""

    case_count: int
    hit_rate_at_k: float
    mean_reciprocal_rank: float
    citation_accuracy: float
    context_expansion_rate: float
    mean_latency_seconds: float


def score_retrieval_runs(runs: list[RetrievalBenchmarkRun]) -> RetrievalMetrics:
    """Calculate focused Phase 6 retrieval metrics from completed benchmark runs."""
    if not runs:
        raise ValueError("At least one retrieval benchmark run is required")
    hits: list[float] = []
    reciprocal_ranks: list[float] = []
    citation_results: list[float] = []
    expansion_results: list[float] = []
    for run in runs:
        if run.latency_seconds < 0:
            raise ValueError("Retrieval benchmark latency cannot be negative")
        rank = next(
            (
                position
                for position, evidence in enumerate(run.evidence, start=1)
                if evidence.source_id == run.case.expected_source_id
            ),
            None,
        )
        hits.append(float(rank is not None))
        reciprocal_ranks.append(0.0 if rank is None else 1.0 / rank)
        citation_results.append(
            float(
                any(
                    citation.source_id == run.case.expected_source_id
                    and citation.locator == run.case.expected_locator
                    for citation in run.citations
                )
            )
        )
        expansion_results.append(
            float(
                any(
                    evidence.source_id == run.case.expected_source_id
                    and run.case.required_context in evidence.content
                    for evidence in run.evidence
                )
            )
        )
    return RetrievalMetrics(
        case_count=len(runs),
        hit_rate_at_k=fmean(hits),
        mean_reciprocal_rank=fmean(reciprocal_ranks),
        citation_accuracy=fmean(citation_results),
        context_expansion_rate=fmean(expansion_results),
        mean_latency_seconds=fmean(run.latency_seconds for run in runs),
    )

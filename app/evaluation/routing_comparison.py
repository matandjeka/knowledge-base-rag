"""Focused Phase 12 routing quality and latency metrics."""

from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True, slots=True)
class RoutingBenchmarkObservation:
    """Expected route and downstream results for one benchmark question."""

    expected_plan_matched: bool
    safe_sql_behavior: bool | None
    routed_hit: bool
    baseline_hit: bool
    routed_reciprocal_rank: float
    baseline_reciprocal_rank: float
    routed_citation_correct: bool
    baseline_citation_correct: bool
    routed_latency_seconds: float
    baseline_latency_seconds: float
    specialized: bool


@dataclass(frozen=True, slots=True)
class RoutingMetrics:
    """Aggregate router correctness, quality deltas, and latency improvement."""

    case_count: int
    plan_accuracy: float
    safe_sql_accuracy: float
    hit_rate_delta: float
    mean_reciprocal_rank_delta: float
    citation_accuracy_delta: float
    specialized_median_latency_improvement: float


def score_routing_observations(
    observations: list[RoutingBenchmarkObservation],
) -> RoutingMetrics:
    """Compare routed retrieval with the default-fusion baseline."""
    if not observations:
        raise ValueError("At least one routing observation is required")
    if any(
        item.routed_latency_seconds < 0 or item.baseline_latency_seconds < 0
        for item in observations
    ):
        raise ValueError("Routing benchmark latency cannot be negative")
    count = len(observations)
    sql_results = [
        item.safe_sql_behavior for item in observations if item.safe_sql_behavior is not None
    ]
    specialized = [item for item in observations if item.specialized]
    if not specialized:
        raise ValueError("At least one specialized routing observation is required")
    routed_median = median(item.routed_latency_seconds for item in specialized)
    baseline_median = median(item.baseline_latency_seconds for item in specialized)
    return RoutingMetrics(
        case_count=count,
        plan_accuracy=sum(item.expected_plan_matched for item in observations) / count,
        safe_sql_accuracy=(
            sum(bool(result) for result in sql_results) / len(sql_results) if sql_results else 1.0
        ),
        hit_rate_delta=(
            sum(item.routed_hit for item in observations)
            - sum(item.baseline_hit for item in observations)
        )
        / count,
        mean_reciprocal_rank_delta=(
            sum(item.routed_reciprocal_rank for item in observations)
            - sum(item.baseline_reciprocal_rank for item in observations)
        )
        / count,
        citation_accuracy_delta=(
            sum(item.routed_citation_correct for item in observations)
            - sum(item.baseline_citation_correct for item in observations)
        )
        / count,
        specialized_median_latency_improvement=baseline_median - routed_median,
    )

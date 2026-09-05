"""Shared deterministic and explicitly enabled live evaluation runner."""

from collections.abc import Awaitable, Callable

from app.evaluation.framework import (
    CaseObservation,
    EvaluationReport,
    EvaluationTier,
    GoldenBenchmark,
    GoldenCase,
    RetrievalConfiguration,
)
from app.evaluation.metrics import score_observations, score_slices

CaseEvaluator = Callable[
    [GoldenCase, RetrievalConfiguration, EvaluationTier], Awaitable[CaseObservation]
]


async def run_evaluation(
    benchmark: GoldenBenchmark,
    configuration: RetrievalConfiguration,
    evaluator: CaseEvaluator,
    *,
    tier: EvaluationTier = EvaluationTier.DETERMINISTIC,
    allow_live: bool = False,
    judge_model: str | None = None,
) -> EvaluationReport:
    """Evaluate every golden case while keeping live execution explicitly opt-in."""
    if tier is EvaluationTier.LIVE and not allow_live:
        raise ValueError("Live evaluation requires explicit allow_live=True")
    observations = [await evaluator(case, configuration, tier) for case in benchmark.cases]
    expected_ids = [case.case_id for case in benchmark.cases]
    observed_ids = [observation.case_id for observation in observations]
    if observed_ids != expected_ids:
        raise ValueError("Evaluator observations must preserve benchmark case order and identity")
    aggregate = score_observations(benchmark, observations, top_k=configuration.top_k)
    slices = score_slices(benchmark, observations, top_k=configuration.top_k)
    return EvaluationReport(
        benchmark_version=benchmark.version,
        benchmark_sha256=benchmark.content_sha256(),
        configuration=configuration,
        tier=tier,
        observations=observations,
        aggregate=aggregate,
        slices=slices,
        judge_model=judge_model,
    )

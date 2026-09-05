"""Unified deterministic metrics, slices, and deployment gates."""

import math
import re
from collections.abc import Iterable
from statistics import fmean, median

from app.evaluation.framework import (
    CaseObservation,
    GateResult,
    GoldenBenchmark,
    GoldenCase,
    MetricSummary,
)
from app.models import SourceType

_WHITESPACE = re.compile(r"\s+")


def score_observations(
    benchmark: GoldenBenchmark,
    observations: list[CaseObservation],
    *,
    top_k: int,
    relevant_source_type: SourceType | None = None,
) -> MetricSummary:
    """Calculate unified metrics for a complete or explicit benchmark subset."""
    if not observations:
        raise ValueError("At least one evaluation observation is required")
    cases = {case.case_id: case for case in benchmark.cases}
    observed_ids = [observation.case_id for observation in observations]
    if len(observed_ids) != len(set(observed_ids)):
        raise ValueError("Evaluation observation case identifiers must be unique")
    unknown = sorted(set(observed_ids) - set(cases))
    if unknown:
        raise ValueError(f"Evaluation observations reference unknown cases: {', '.join(unknown)}")
    scores = [
        _score_case(cases[item.case_id], item, top_k, relevant_source_type) for item in observations
    ]
    latencies = sorted(item.latency_seconds for item in observations)
    costs = [item.cost_usd for item in observations if item.cost_usd is not None]
    judge_scores = [item.judge_faithful for item in observations if item.judge_faithful is not None]
    sql_scores = [score.sql_safety for score in scores if score.sql_safety is not None]
    return MetricSummary(
        case_count=len(scores),
        recall_at_k=fmean(score.recall for score in scores),
        precision_at_k=fmean(score.precision for score in scores),
        hit_rate_at_k=fmean(score.hit for score in scores),
        mean_reciprocal_rank=fmean(score.reciprocal_rank for score in scores),
        ndcg_at_k=fmean(score.ndcg for score in scores),
        citation_accuracy=fmean(score.citation_accuracy for score in scores),
        faithfulness=fmean(score.faithfulness for score in scores),
        answer_relevance=fmean(score.answer_relevance for score in scores),
        route_accuracy=fmean(score.route_accuracy for score in scores),
        sql_safety_accuracy=fmean(sql_scores) if sql_scores else 1.0,
        median_latency_seconds=median(latencies),
        p95_latency_seconds=latencies[math.ceil(len(latencies) * 0.95) - 1],
        mean_cost_usd=fmean(costs) if costs else None,
        llm_faithfulness=(fmean(float(score) for score in judge_scores) if judge_scores else None),
    )


def score_slices(
    benchmark: GoldenBenchmark,
    observations: list[CaseObservation],
    *,
    top_k: int,
) -> dict[str, MetricSummary]:
    """Return source, intent, answerability, and specialization slices."""
    cases = {case.case_id: case for case in benchmark.cases}
    groups: dict[str, list[CaseObservation]] = {}
    source_groups: dict[str, tuple[SourceType, list[CaseObservation]]] = {}
    for observation in observations:
        case = cases.get(observation.case_id)
        if case is None:
            raise ValueError(
                f"Evaluation observation references unknown case: {observation.case_id}"
            )
        keys = {
            f"intent:{case.intent.value}",
            "answerability:insufficient"
            if case.expected_insufficient
            else "answerability:answerable",
            "route:specialized" if case.specialized else "route:default",
        }
        for key in keys:
            groups.setdefault(key, []).append(observation)
        for source_type in {item.source_type for item in case.expected_evidence}:
            key = f"source:{source_type.value}"
            _, group = source_groups.setdefault(key, (source_type, []))
            group.append(observation)
    results = {
        key: score_observations(benchmark, group, top_k=top_k)
        for key, group in sorted(groups.items())
    }
    results.update(
        {
            key: score_observations(
                benchmark,
                group,
                top_k=top_k,
                relevant_source_type=source_type,
            )
            for key, (source_type, group) in sorted(source_groups.items())
        }
    )
    return results


def compare_with_baseline(
    candidate: MetricSummary,
    baseline: MetricSummary,
    candidate_slices: dict[str, MetricSummary],
    baseline_slices: dict[str, MetricSummary],
) -> list[GateResult]:
    """Apply the agreed aggregate, slice, safety, and latency deployment gates."""
    gates = [
        _minimum_delta_gate("recall_at_k_delta", candidate.recall_at_k, baseline.recall_at_k, 0.02),
        _minimum_delta_gate(
            "mean_reciprocal_rank_delta",
            candidate.mean_reciprocal_rank,
            baseline.mean_reciprocal_rank,
            0.02,
        ),
        _exact_gate("citation_accuracy", candidate.citation_accuracy, 1.0),
        _exact_gate("faithfulness", candidate.faithfulness, 1.0),
        _maximum_ratio_gate(
            "median_latency_regression",
            candidate.median_latency_seconds,
            baseline.median_latency_seconds,
            1.10,
        ),
        _exact_gate("sql_safety_accuracy", candidate.sql_safety_accuracy, 1.0),
    ]
    default_slice = candidate_slices.get("intent:default")
    if default_slice is None:
        gates.append(
            GateResult(
                gate="routing_ambiguity_behavior",
                passed=False,
                observed=0.0,
                required="default-intent slice present with route_accuracy = 1.0",
            )
        )
    else:
        gates.append(_exact_gate("routing_ambiguity_behavior", default_slice.route_accuracy, 1.0))
    quality_fields = (
        "recall_at_k",
        "mean_reciprocal_rank",
        "citation_accuracy",
        "faithfulness",
        "route_accuracy",
    )
    for key in sorted(set(candidate_slices) | set(baseline_slices)):
        candidate_slice = candidate_slices.get(key)
        baseline_slice = baseline_slices.get(key)
        for field in quality_fields:
            observed = (
                getattr(candidate_slice, field) - getattr(baseline_slice, field)
                if candidate_slice is not None and baseline_slice is not None
                else -1.0
            )
            gates.append(
                GateResult(
                    gate=f"slice_{field}_delta:{key}",
                    passed=observed >= -0.05 or math.isclose(observed, -0.05, abs_tol=1e-12),
                    observed=observed,
                    required=">= -0.05",
                )
            )
        if candidate_slice is None or baseline_slice is None:
            gates.append(
                GateResult(
                    gate=f"slice_latency_regression:{key}",
                    passed=False,
                    observed=-1.0,
                    required="<= 1.1",
                )
            )
        else:
            gates.append(
                _maximum_ratio_gate(
                    f"slice_latency_regression:{key}",
                    candidate_slice.median_latency_seconds,
                    baseline_slice.median_latency_seconds,
                    1.10,
                )
            )
    return gates


class _CaseScores:
    def __init__(
        self,
        *,
        recall: float,
        precision: float,
        hit: float,
        reciprocal_rank: float,
        ndcg: float,
        citation_accuracy: float,
        faithfulness: float,
        answer_relevance: float,
        route_accuracy: float,
        sql_safety: float | None,
    ) -> None:
        self.recall = recall
        self.precision = precision
        self.hit = hit
        self.reciprocal_rank = reciprocal_rank
        self.ndcg = ndcg
        self.citation_accuracy = citation_accuracy
        self.faithfulness = faithfulness
        self.answer_relevance = answer_relevance
        self.route_accuracy = route_accuracy
        self.sql_safety = sql_safety


def _score_case(
    case: GoldenCase,
    observation: CaseObservation,
    top_k: int,
    relevant_source_type: SourceType | None,
) -> _CaseScores:
    expected = {
        item.label: item
        for item in case.expected_evidence
        if relevant_source_type is None or item.source_type is relevant_source_type
    }
    if observation.error_category is not None:
        return _CaseScores(
            recall=0.0,
            precision=0.0,
            hit=0.0,
            reciprocal_rank=0.0,
            ndcg=0.0,
            citation_accuracy=0.0,
            faithfulness=0.0,
            answer_relevance=0.0,
            route_accuracy=0.0,
            sql_safety=0.0 if case.sql_safe_required else None,
        )
    relevant = set(expected)
    retrieved = observation.retrieved_evidence_labels[:top_k]
    retrieved_relevant = [label for label in retrieved if label in relevant]
    if case.expected_insufficient:
        correct_empty = float(observation.insufficient_evidence and not retrieved)
        recall = precision = hit = reciprocal_rank = ndcg = correct_empty
    else:
        recall = len(set(retrieved_relevant)) / len(relevant)
        precision = len(retrieved_relevant) / len(retrieved) if retrieved else 0.0
        hit = float(bool(retrieved_relevant))
        first_rank = next(
            (rank for rank, label in enumerate(retrieved, start=1) if label in relevant), None
        )
        reciprocal_rank = 0.0 if first_rank is None else 1.0 / first_rank
        actual_grades = [
            expected[label].relevance if label in expected else 0 for label in retrieved
        ]
        ideal_grades = sorted((item.relevance for item in expected.values()), reverse=True)[:top_k]
        ideal_dcg = _dcg(ideal_grades)
        ndcg = 0.0 if ideal_dcg == 0 else _dcg(actual_grades) / ideal_dcg
    cited = set(observation.cited_evidence_labels)
    citation_accuracy = float(
        not cited
        if case.expected_insufficient
        else bool(cited) and cited <= relevant & set(retrieved)
    )
    faithfulness = _faithfulness(case, observation)
    required = {_normalize(fact) for fact in case.required_facts}
    answer_text = _normalize(" ".join(segment.text for segment in observation.answer_segments))
    answer_relevance = float(
        observation.insufficient_evidence
        if case.expected_insufficient
        else not observation.insufficient_evidence and all(fact in answer_text for fact in required)
    )
    route_accuracy = float(
        observation.routed_intent is case.intent
        and observation.selected_retrievers == case.expected_retrievers
    )
    sql_safety = float(observation.safe_sql_behavior is True) if case.sql_safe_required else None
    return _CaseScores(
        recall=recall,
        precision=precision,
        hit=hit,
        reciprocal_rank=reciprocal_rank,
        ndcg=ndcg,
        citation_accuracy=citation_accuracy,
        faithfulness=faithfulness,
        answer_relevance=answer_relevance,
        route_accuracy=route_accuracy,
        sql_safety=sql_safety,
    )


def _faithfulness(case: GoldenCase, observation: CaseObservation) -> float:
    if case.expected_insufficient:
        return float(observation.insufficient_evidence and not observation.answer_segments)
    expected = {item.label: item for item in case.expected_evidence}
    contradictions = [_normalize(item) for item in case.contradictions]
    for segment in observation.answer_segments:
        cited = [expected[label] for label in segment.cited_evidence_labels if label in expected]
        if len(cited) != len(set(segment.cited_evidence_labels)) or not cited:
            return 0.0
        supported = [_normalize(fact) for evidence in cited for fact in evidence.supporting_facts]
        segment_text = _normalize(segment.text)
        if any(contradiction in segment_text for contradiction in contradictions):
            return 0.0
        claims = [
            _normalize(claim) for claim in re.split(r"[.!?]+", segment.text) if _normalize(claim)
        ]
        if not claims or any(claim not in supported for claim in claims):
            return 0.0
    return float(bool(observation.answer_segments))


def _normalize(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip().casefold()


def _dcg(grades: Iterable[int]) -> float:
    return float(sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(grades, 1)))


def _minimum_delta_gate(
    name: str, candidate: float, baseline: float, tolerance: float
) -> GateResult:
    delta = candidate - baseline
    return GateResult(
        gate=name,
        passed=delta >= -tolerance or math.isclose(delta, -tolerance, abs_tol=1e-12),
        observed=delta,
        required=f">= -{tolerance}",
    )


def _exact_gate(name: str, observed: float, expected: float) -> GateResult:
    return GateResult(
        gate=name, passed=observed == expected, observed=observed, required=f"= {expected}"
    )


def _maximum_ratio_gate(name: str, candidate: float, baseline: float, ratio: float) -> GateResult:
    if baseline == 0:
        return GateResult(
            gate=name,
            passed=candidate == 0,
            observed=candidate,
            required="= 0 when baseline is 0",
        )
    observed = candidate / baseline
    return GateResult(
        gate=name, passed=observed <= ratio, observed=observed, required=f"<= {ratio}"
    )

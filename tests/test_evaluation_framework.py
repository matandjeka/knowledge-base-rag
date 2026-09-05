"""Phase 14 unified benchmark, metrics, runner, gate, and report tests."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.evaluation.cli import main as evaluation_main
from app.evaluation.execution import (
    FaithfulnessJudgment,
    OpenAIFaithfulnessJudge,
    QueryServiceEvaluator,
    build_deterministic_query_service,
)
from app.evaluation.framework import (
    CaseObservation,
    EvaluationReport,
    EvaluationTier,
    GoldenBenchmark,
    GoldenCase,
    MetricSummary,
    ObservedAnswerSegment,
    RetrievalConfiguration,
)
from app.evaluation.metrics import compare_with_baseline, score_observations, score_slices
from app.evaluation.reporting import attach_baseline, load_benchmark, load_report, write_report
from app.evaluation.runner import run_evaluation
from app.models import FusionStrategy, RetrievalMode, RoutingIntent, SourceType

FIXTURE = Path(__file__).parent / "fixtures" / "golden_benchmark.json"


def _benchmark() -> GoldenBenchmark:
    return load_benchmark(FIXTURE)


def _configuration(name: str = "candidate") -> RetrievalConfiguration:
    return RetrievalConfiguration(
        name=name,
        retrieval_mode=RetrievalMode.AUTO,
        retrievers=[
            RetrievalMode.VECTOR,
            RetrievalMode.SENTENCE_WINDOW,
            RetrievalMode.LEXICAL,
            RetrievalMode.GRAPH,
            RetrievalMode.SQL,
        ],
        top_k=5,
    )


def _perfect_observation(case: GoldenCase, latency: float = 0.1) -> CaseObservation:
    labels = [item.label for item in case.expected_evidence]
    segments = (
        [
            ObservedAnswerSegment(
                text=". ".join(
                    fact for item in case.expected_evidence for fact in item.supporting_facts
                ),
                cited_evidence_labels=labels,
            )
        ]
        if labels
        else []
    )
    return CaseObservation(
        case_id=case.case_id,
        retrieved_evidence_labels=labels,
        cited_evidence_labels=labels,
        answer_segments=segments,
        routed_intent=case.intent,
        selected_retrievers=case.expected_retrievers,
        insufficient_evidence=case.expected_insufficient,
        safe_sql_behavior=True if case.sql_safe_required else None,
        latency_seconds=latency,
    )


def _perfect_report(name: str = "candidate", latency: float = 0.1) -> EvaluationReport:
    benchmark = _benchmark()
    observations = [_perfect_observation(case, latency) for case in benchmark.cases]
    configuration = _configuration(name)
    return EvaluationReport(
        benchmark_version=benchmark.version,
        benchmark_sha256=benchmark.content_sha256(),
        configuration=configuration,
        tier=EvaluationTier.DETERMINISTIC,
        created_at=datetime(2026, 9, 4, tzinfo=UTC),
        observations=observations,
        aggregate=score_observations(benchmark, observations, top_k=configuration.top_k),
        slices=score_slices(benchmark, observations, top_k=configuration.top_k),
    )


def test_golden_benchmark_is_valid_complete_and_stably_hashed() -> None:
    benchmark = _benchmark()
    reparsed = GoldenBenchmark.model_validate_json(
        json.dumps(benchmark.model_dump(mode="json"), indent=4, sort_keys=True)
    )

    assert len(benchmark.cases) == 40
    assert {case.intent for case in benchmark.cases} == set(RoutingIntent)
    assert {
        evidence.source_type for case in benchmark.cases for evidence in case.expected_evidence
    } == set(SourceType)
    assert benchmark.content_sha256() == reparsed.content_sha256()


def test_benchmark_rejects_duplicate_cases_and_wrong_locator_types() -> None:
    payload = json.loads(FIXTURE.read_text())
    payload["cases"][1]["case_id"] = payload["cases"][0]["case_id"]
    with pytest.raises(ValidationError, match="case identifiers must be unique"):
        GoldenBenchmark.model_validate(payload)

    payload = json.loads(FIXTURE.read_text())
    payload["cases"][0]["expected_evidence"][0]["locator"] = "row wrong"
    with pytest.raises(ValidationError, match="locator does not match"):
        GoldenBenchmark.model_validate(payload)


def test_perfect_run_scores_every_metric_and_slice_at_one() -> None:
    benchmark = _benchmark()
    observations = [_perfect_observation(case) for case in benchmark.cases]

    metrics = score_observations(benchmark, observations, top_k=5)
    slices = score_slices(benchmark, observations, top_k=5)

    assert metrics.recall_at_k == 1
    assert metrics.precision_at_k == 1
    assert metrics.hit_rate_at_k == 1
    assert metrics.mean_reciprocal_rank == 1
    assert metrics.ndcg_at_k == 1
    assert metrics.citation_accuracy == 1
    assert metrics.faithfulness == 1
    assert metrics.answer_relevance == 1
    assert metrics.route_accuracy == 1
    assert metrics.sql_safety_accuracy == 1
    assert {f"source:{item.value}" for item in SourceType} <= set(slices)
    assert {f"intent:{item.value}" for item in RoutingIntent} <= set(slices)


def test_recall_precision_mrr_and_graded_ndcg_handle_ranked_noise() -> None:
    benchmark = _benchmark()
    case = next(item for item in benchmark.cases if item.case_id == "default_remote_work")
    labels = [item.label for item in case.expected_evidence]
    observation = _perfect_observation(case).model_copy(
        update={"retrieved_evidence_labels": ["noise", labels[1], labels[0]]}
    )

    metrics = score_observations(benchmark, [observation], top_k=3)

    assert metrics.recall_at_k == 1
    assert metrics.precision_at_k == pytest.approx(2 / 3)
    assert metrics.mean_reciprocal_rank == 0.5
    assert 0 < metrics.ndcg_at_k < 1


def test_faithfulness_rejects_unsupported_and_contradictory_facts() -> None:
    benchmark = _benchmark()
    case = benchmark.cases[0]
    unsupported = _perfect_observation(case).model_copy(
        update={
            "answer_segments": [
                ObservedAnswerSegment(
                    text=(
                        f"{case.expected_evidence[0].supporting_facts[0]}. "
                        "An unsupported assertion."
                    ),
                    cited_evidence_labels=[case.expected_evidence[0].label],
                )
            ]
        }
    )
    contradictory_case = case.model_copy(update={"contradictions": ["Opposite claim"]})
    contradictory_benchmark = benchmark.model_copy(
        update={"cases": [contradictory_case, *benchmark.cases[1:]]}
    )
    contradictory = unsupported.model_copy(
        update={
            "answer_segments": [
                ObservedAnswerSegment(
                    text="Opposite claim.",
                    cited_evidence_labels=[case.expected_evidence[0].label],
                )
            ]
        }
    )

    assert score_observations(benchmark, [unsupported], top_k=5).faithfulness == 0
    assert score_observations(contradictory_benchmark, [contradictory], top_k=5).faithfulness == 0


def test_deployment_gates_accept_boundaries_and_reject_slice_regression() -> None:
    baseline = _perfect_report("baseline")
    candidate_metrics = baseline.aggregate.model_copy(
        update={
            "recall_at_k": 0.98,
            "mean_reciprocal_rank": 0.98,
            "median_latency_seconds": 0.11,
        }
    )
    candidate_slices = dict(baseline.slices)
    candidate_slices["source:pdf"] = candidate_slices["source:pdf"].model_copy(
        update={"recall_at_k": 0.94}
    )

    gates = compare_with_baseline(
        candidate_metrics, baseline.aggregate, candidate_slices, baseline.slices
    )

    assert next(gate for gate in gates if gate.gate == "recall_at_k_delta").passed
    assert next(gate for gate in gates if gate.gate == "median_latency_regression").passed
    assert not next(
        gate for gate in gates if gate.gate == "slice_recall_at_k_delta:source:pdf"
    ).passed


def test_source_slices_score_only_their_own_expected_evidence() -> None:
    benchmark = _benchmark()
    case = next(item for item in benchmark.cases if item.case_id == "default_remote_work")
    pdf = next(item for item in case.expected_evidence if item.source_type is SourceType.PDF)
    observation = CaseObservation(
        case_id=case.case_id,
        retrieved_evidence_labels=[pdf.label],
        cited_evidence_labels=[pdf.label],
        answer_segments=[
            ObservedAnswerSegment(text=pdf.supporting_facts[0], cited_evidence_labels=[pdf.label])
        ],
        routed_intent=case.intent,
        selected_retrievers=case.expected_retrievers,
        latency_seconds=0.1,
    )

    slices = score_slices(benchmark, [observation], top_k=5)

    assert slices["source:pdf"].recall_at_k == 1
    assert slices["source:website"].recall_at_k == 0


def test_report_comparison_round_trips_without_question_or_fact_content(tmp_path: Path) -> None:
    baseline = _perfect_report("baseline")
    candidate = attach_baseline(_perfect_report(), baseline)

    path = write_report(candidate, tmp_path)
    restored = load_report(path)
    serialized = path.read_text()

    assert restored.configuration == candidate.configuration
    assert restored.aggregate == candidate.aggregate
    assert all(
        segment.text == "[redacted]"
        for observation in restored.observations
        for segment in observation.answer_segments
    )
    assert restored.passed
    assert "What does HR-402 require?" not in serialized
    assert "HR-402 requires annual access reviews" not in serialized
    with pytest.raises(FileExistsError):
        write_report(candidate, tmp_path)


@pytest.mark.asyncio
async def test_runner_is_deterministic_and_live_execution_requires_opt_in() -> None:
    benchmark = _benchmark()
    configuration = _configuration()

    async def evaluator(
        case: GoldenCase,
        received: RetrievalConfiguration,
        tier: EvaluationTier,
    ) -> CaseObservation:
        assert received == configuration
        assert tier is EvaluationTier.DETERMINISTIC
        return _perfect_observation(case)

    report = await run_evaluation(benchmark, configuration, evaluator)

    assert report.aggregate.faithfulness == 1
    assert [item.case_id for item in report.observations] == [
        case.case_id for case in benchmark.cases
    ]
    with pytest.raises(ValueError, match="explicit allow_live"):
        await run_evaluation(
            benchmark,
            configuration,
            evaluator,
            tier=EvaluationTier.LIVE,
        )


@pytest.mark.asyncio
async def test_deterministic_runner_executes_query_service_against_committed_corpus() -> None:
    benchmark = _benchmark()
    service = await build_deterministic_query_service(benchmark)
    evaluator = QueryServiceEvaluator(service)

    report = await run_evaluation(benchmark, _configuration(), evaluator)

    assert report.aggregate.recall_at_k == 1
    assert report.aggregate.citation_accuracy == 1
    assert report.aggregate.faithfulness == 1
    assert report.aggregate.sql_safety_accuracy == 1
    assert all(observation.error_category is None for observation in report.observations)


def test_execution_errors_force_case_metrics_to_zero() -> None:
    benchmark = _benchmark()
    case = benchmark.cases[0]
    observation = _perfect_observation(case).model_copy(
        update={"error_category": "retrieval_error"}
    )

    metrics = score_observations(benchmark, [observation], top_k=5)

    assert metrics.recall_at_k == 0
    assert metrics.citation_accuracy == 0
    assert metrics.faithfulness == 0


def test_configuration_and_error_categories_exclude_secret_bearing_text() -> None:
    with pytest.raises(ValidationError):
        RetrievalConfiguration(
            name="candidate with token=secret",
            retrieval_mode=RetrievalMode.FUSION,
            retrievers=[RetrievalMode.VECTOR, RetrievalMode.LEXICAL],
            fusion_strategy=FusionStrategy.RRF,
        )
    with pytest.raises(ValidationError):
        CaseObservation(case_id="case", latency_seconds=0, error_category="token=secret value")
    with pytest.raises(ValidationError):
        CaseObservation(
            case_id="case",
            latency_seconds=0,
            retrieved_evidence_labels=["safe", "safe"],
        )


@pytest.mark.parametrize(
    ("source_type", "locator"),
    [
        ("pdf", "page unknown"),
        ("website", "javascript:alert(1)"),
        ("csv", "row "),
        ("database", "table "),
    ],
)
def test_benchmark_rejects_malformed_source_locators(source_type: str, locator: str) -> None:
    payload = json.loads(FIXTURE.read_text())
    evidence = next(
        item
        for case in payload["cases"]
        for item in case["expected_evidence"]
        if item["source_type"] == source_type
    )
    evidence["locator"] = locator

    with pytest.raises(ValidationError, match="locator does not match"):
        GoldenBenchmark.model_validate(payload)


def test_cli_requires_baseline_or_explicit_baseline_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rag-evaluate",
            "--benchmark",
            str(FIXTURE),
            "--configuration",
            "configuration.json",
            "--output-directory",
            "reports",
        ],
    )

    with pytest.raises(SystemExit) as captured:
        evaluation_main()

    assert captured.value.code == 2


def test_cli_executes_deterministic_baseline_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = tmp_path / "configuration.json"
    configuration.write_text(_configuration("baseline").model_dump_json())
    output = tmp_path / "reports"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rag-evaluate",
            "--benchmark",
            str(FIXTURE),
            "--configuration",
            str(configuration),
            "--output-directory",
            str(output),
            "--establish-baseline",
        ],
    )

    assert evaluation_main() == 0
    reports = list(output.glob("*.json"))
    assert len(reports) == 1
    assert load_report(reports[0]).aggregate.faithfulness == 1


@pytest.mark.asyncio
async def test_openai_judge_uses_structured_output_contract() -> None:
    class Responses:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def parse(self, **kwargs: Any) -> SimpleNamespace:
            self.calls.append(kwargs)
            return SimpleNamespace(
                output_parsed=FaithfulnessJudgment(
                    faithful=True, rationale="The excerpt directly supports the segment."
                )
            )

    responses = Responses()
    client = SimpleNamespace(responses=responses)
    judge = OpenAIFaithfulnessJudge(
        api_key="unused",
        model_name="judge-model",
        timeout_seconds=10,
        max_retries=0,
        client=client,
    )

    result = await judge.judge("Supported fact.", ["Supported fact."])

    assert result.faithful
    assert responses.calls[0]["text_format"] is FaithfulnessJudgment


def test_metric_summary_rejects_out_of_range_values() -> None:
    with pytest.raises(ValidationError):
        MetricSummary(
            case_count=1,
            recall_at_k=1.1,
            precision_at_k=1,
            hit_rate_at_k=1,
            mean_reciprocal_rank=1,
            ndcg_at_k=1,
            citation_accuracy=1,
            faithfulness=1,
            answer_relevance=1,
            route_accuracy=1,
            sql_safety_accuracy=1,
            median_latency_seconds=0,
            p95_latency_seconds=0,
        )

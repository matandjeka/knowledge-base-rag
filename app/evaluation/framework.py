"""Unified Phase 14 benchmark, observation, metric, and report contracts."""

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models import FusionStrategy, RetrievalMode, RoutingIntent, SourceType

EvaluationLabel = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9:_.-]*$",
    ),
]


class EvaluationTier(StrEnum):
    """Execution environments with different reproducibility guarantees."""

    DETERMINISTIC = "deterministic"
    LIVE = "live"


class ExpectedEvidence(BaseModel):
    """One stable logical evidence label and its relevance judgment."""

    model_config = ConfigDict(extra="forbid")

    label: EvaluationLabel
    source_type: SourceType
    locator: str = Field(min_length=1, max_length=500)
    relevance: int = Field(ge=1, le=3)
    supporting_facts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_locator(self) -> Self:
        """Keep logical labels aligned with source-specific citation locations."""
        patterns = {
            SourceType.PDF: r"^page [1-9][0-9]*$",
            SourceType.WEBSITE: r"^https?://[^\s]+$",
            SourceType.CSV: r"^row \S(?:.*\S)?$",
            SourceType.DATABASE: r"^table \S(?:.*\S)?$",
        }[self.source_type]
        if re.fullmatch(patterns, self.locator) is None:
            raise ValueError("Expected evidence locator does not match its source type")
        if not self.supporting_facts:
            raise ValueError("Expected evidence requires at least one supporting fact")
        return self


class GoldenCase(BaseModel):
    """One versioned evaluation question with complete expected behavior."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    question: str = Field(min_length=1, max_length=4000)
    intent: RoutingIntent
    expected_evidence: list[ExpectedEvidence] = Field(default_factory=list)
    required_facts: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    expected_retrievers: list[RetrievalMode] = Field(min_length=1)
    expected_insufficient: bool = False
    specialized: bool = False
    sql_safe_required: bool = False

    @model_validator(mode="after")
    def validate_expectations(self) -> Self:
        labels = [item.label for item in self.expected_evidence]
        if len(labels) != len(set(labels)):
            raise ValueError("Expected evidence labels must be unique within a case")
        if self.expected_insufficient:
            if self.expected_evidence or self.required_facts:
                raise ValueError("Insufficient-evidence cases cannot require evidence or facts")
        elif not self.expected_evidence:
            raise ValueError("Answerable cases require at least one expected evidence label")
        if self.sql_safe_required and self.intent is not RoutingIntent.SQL:
            raise ValueError("SQL safety can only be required for SQL intent cases")
        if len(self.expected_retrievers) != len(set(self.expected_retrievers)):
            raise ValueError("Expected retrievers must be unique")
        return self


class GoldenBenchmark(BaseModel):
    """Committed deployment-gate benchmark with stable content hashing."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    cases: list[GoldenCase] = Field(min_length=30, max_length=50)

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Golden benchmark case identifiers must be unique")
        source_types = {
            evidence.source_type for case in self.cases for evidence in case.expected_evidence
        }
        if source_types != set(SourceType):
            raise ValueError("Golden benchmark must cover every source type")
        intents = {case.intent for case in self.cases}
        if intents != set(RoutingIntent):
            raise ValueError("Golden benchmark must cover every routing intent")
        if not any(case.expected_insufficient for case in self.cases):
            raise ValueError("Golden benchmark must include insufficient-evidence behavior")
        if not any(case.sql_safe_required for case in self.cases):
            raise ValueError("Golden benchmark must include SQL safety behavior")
        return self

    def content_sha256(self) -> str:
        """Return a stable hash independent of JSON whitespace and key ordering."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class RetrievalConfiguration(BaseModel):
    """Named and reproducible retrieval configuration under evaluation."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    retrieval_mode: RetrievalMode
    retrievers: list[RetrievalMode] = Field(min_length=1)
    fusion_strategy: FusionStrategy | None = None
    rerank: bool = False
    top_k: int = Field(default=5, ge=1, le=20)
    min_similarity: float = Field(default=0.0, ge=0)
    routing_confidence_threshold: float = Field(default=0.70, ge=0, le=1)
    routing_winning_margin: float = Field(default=0.15, ge=0, le=1)

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        if len(self.retrievers) != len(set(self.retrievers)):
            raise ValueError("Configuration retrievers must be unique")
        if self.retrieval_mode is RetrievalMode.FUSION and len(self.retrievers) < 2:
            raise ValueError("Fusion evaluation requires at least two retrievers")
        if self.retrieval_mode is not RetrievalMode.FUSION and self.fusion_strategy is not None:
            raise ValueError("Fusion strategy requires fusion retrieval mode")
        return self


class ObservedAnswerSegment(BaseModel):
    """One asserted fact set with stable logical citation labels."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)
    cited_evidence_labels: list[EvaluationLabel] = Field(min_length=1)


class CaseObservation(BaseModel):
    """Normalized result of evaluating one case with one configuration."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    retrieved_evidence_labels: list[EvaluationLabel] = Field(default_factory=list)
    cited_evidence_labels: list[EvaluationLabel] = Field(default_factory=list)
    answer_segments: list[ObservedAnswerSegment] = Field(default_factory=list)
    routed_intent: RoutingIntent | None = None
    selected_retrievers: list[RetrievalMode] = Field(default_factory=list)
    insufficient_evidence: bool = False
    safe_sql_behavior: bool | None = None
    latency_seconds: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    judge_faithful: bool | None = None
    error_category: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_.-]*$"
    )

    @model_validator(mode="after")
    def validate_labels(self) -> Self:
        if len(self.retrieved_evidence_labels) != len(set(self.retrieved_evidence_labels)):
            raise ValueError("Retrieved evidence labels must be unique")
        if len(self.cited_evidence_labels) != len(set(self.cited_evidence_labels)):
            raise ValueError("Cited evidence labels must be unique")
        return self


class MetricSummary(BaseModel):
    """Quality, provenance, safety, performance, and optional cost metrics."""

    model_config = ConfigDict(extra="forbid")

    case_count: int = Field(ge=1)
    recall_at_k: float = Field(ge=0, le=1)
    precision_at_k: float = Field(ge=0, le=1)
    hit_rate_at_k: float = Field(ge=0, le=1)
    mean_reciprocal_rank: float = Field(ge=0, le=1)
    ndcg_at_k: float = Field(ge=0, le=1)
    citation_accuracy: float = Field(ge=0, le=1)
    faithfulness: float = Field(ge=0, le=1)
    answer_relevance: float = Field(ge=0, le=1)
    route_accuracy: float = Field(ge=0, le=1)
    sql_safety_accuracy: float = Field(ge=0, le=1)
    median_latency_seconds: float = Field(ge=0)
    p95_latency_seconds: float = Field(ge=0)
    mean_cost_usd: float | None = Field(default=None, ge=0)
    llm_faithfulness: float | None = Field(default=None, ge=0, le=1)


class GateResult(BaseModel):
    """One explicit deployment criterion and its observed result."""

    model_config = ConfigDict(extra="forbid")

    gate: str = Field(min_length=1)
    passed: bool
    observed: float
    required: str = Field(min_length=1)


class EvaluationReport(BaseModel):
    """Immutable and portable evaluation result artifact."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    benchmark_version: str
    benchmark_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    configuration: RetrievalConfiguration
    tier: EvaluationTier
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    observations: list[CaseObservation] = Field(min_length=1)
    aggregate: MetricSummary
    slices: dict[str, MetricSummary] = Field(default_factory=dict)
    baseline_configuration: str | None = None
    gates: list[GateResult] = Field(default_factory=list)
    passed: bool | None = None
    judge_model: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    )

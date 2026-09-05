"""Inspectable contracts for deterministic query routing."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.query import RetrievalMode


class RoutingIntent(StrEnum):
    """Question intent families recognized by the rules router."""

    SQL = "sql"
    GRAPH = "graph"
    SENTENCE_WINDOW = "sentence_window"
    LEXICAL = "lexical"
    DEFAULT = "default"


class RoutingKind(StrEnum):
    """Whether retrieval was classified or explicitly selected."""

    AUTO = "auto"
    OVERRIDE = "override"


class RoutingRuleMatch(BaseModel):
    """One inspectable routing signal and its score contribution."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1)
    intent: RoutingIntent
    score: float = Field(gt=0, le=1)
    matched_text: str = Field(min_length=1)


class RoutingTrace(BaseModel):
    """Request-local explanation of the selected retrieval plan."""

    model_config = ConfigDict(extra="forbid")

    kind: RoutingKind
    intent: RoutingIntent
    confidence: float = Field(ge=0, le=1)
    selected_retrievers: list[RetrievalMode] = Field(min_length=1)
    selected_source_ids: list[UUID] = Field(default_factory=list)
    matched_rules: list[RoutingRuleMatch] = Field(default_factory=list)
    fallback_reason: str | None = None
    routing_latency_ms: float = Field(ge=0)

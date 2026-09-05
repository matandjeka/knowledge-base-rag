"""Query, citation, and grounded-response contracts."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.documents import Evidence
from app.models.sources import SourceType


class RetrievalMode(StrEnum):
    """Independently selectable retrieval representations."""

    VECTOR = "vector"
    SENTENCE_WINDOW = "sentence_window"
    GRAPH = "graph"
    LEXICAL = "lexical"
    FUSION = "fusion"
    SQL = "sql"
    AUTO = "auto"


class FusionStrategy(StrEnum):
    """Supported rank-based candidate fusion algorithms."""

    RRF = "rrf"
    WEIGHTED_RRF = "weighted_rrf"


class FusionContribution(BaseModel):
    """Inspectable contribution from one retriever to fused evidence."""

    model_config = ConfigDict(extra="forbid")

    retriever: RetrievalMode
    rank: int = Field(ge=1)
    evidence_id: UUID
    raw_score: float | None = None
    normalized_score: float | None = Field(default=None, ge=0, le=1)
    weight: float = Field(ge=0)
    contribution: float = Field(ge=0)


class QueryRequest(BaseModel):
    """Workspace-scoped retrieval request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workspace_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    question: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    source_ids: list[UUID] = Field(default_factory=list)
    retrieval_mode: RetrievalMode = RetrievalMode.VECTOR
    fusion_retrievers: list[RetrievalMode] | None = None
    fusion_strategy: FusionStrategy | None = None
    rerank: bool = False

    @model_validator(mode="after")
    def validate_fusion_options(self) -> "QueryRequest":
        """Require coherent fusion-only request options."""
        if self.retrieval_mode is RetrievalMode.SQL and len(self.source_ids) != 1:
            raise ValueError("SQL retrieval requires exactly one database source_id")
        if self.retrieval_mode is not RetrievalMode.FUSION:
            if (
                self.fusion_retrievers is not None
                or self.fusion_strategy is not None
                or self.rerank
            ):
                raise ValueError("Fusion options require retrieval_mode='fusion'")
            return self
        retrievers = self.fusion_retrievers
        if retrievers is not None:
            if len(retrievers) < 2:
                raise ValueError("Fused retrieval requires at least two retrievers")
            if len(set(retrievers)) != len(retrievers):
                raise ValueError("Fusion retrievers must be distinct")
            if RetrievalMode.FUSION in retrievers:
                raise ValueError("Fusion cannot include itself as a retriever")
        return self


class Citation(BaseModel):
    """Request-local locator for one retrieved evidence item."""

    model_config = ConfigDict(extra="forbid")

    citation_id: str = Field(pattern=r"^S[1-9][0-9]*$")
    evidence_id: UUID
    source_id: UUID
    source_type: SourceType
    source_title: str | None = None
    excerpt: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    score: float


class QueryResponse(BaseModel):
    """Deterministic grounded response plus inspectable retrieval evidence."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    insufficient_evidence: bool
    routing_trace: "RoutingTrace | None" = None


class SourceIndexResult(BaseModel):
    """Summary of an explicit workspace vector-index rebuild."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    source_id: UUID
    document_count: int = Field(ge=1)
    generation_id: UUID


from app.models.routing import RoutingTrace  # noqa: E402

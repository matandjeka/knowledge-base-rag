"""Query, citation, and grounded-response contracts."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.documents import Evidence
from app.models.sources import SourceType


class RetrievalMode(StrEnum):
    """Independently selectable retrieval representations."""

    VECTOR = "vector"
    SENTENCE_WINDOW = "sentence_window"
    GRAPH = "graph"


class QueryRequest(BaseModel):
    """Workspace-scoped retrieval request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workspace_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    question: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    source_ids: list[UUID] = Field(default_factory=list)
    retrieval_mode: RetrievalMode = RetrievalMode.VECTOR


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
    score: float = Field(ge=-1, le=1)


class QueryResponse(BaseModel):
    """Deterministic grounded response plus inspectable retrieval evidence."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    insufficient_evidence: bool


class SourceIndexResult(BaseModel):
    """Summary of an explicit workspace vector-index rebuild."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    source_id: UUID
    document_count: int = Field(ge=1)
    generation_id: UUID

"""Canonical source-document and retrieval-evidence models."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.models.sources import SourceType


class RawDocument(BaseModel):
    """Content emitted directly by a source connector."""

    model_config = ConfigDict(extra="forbid")

    source_id: UUID
    content: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NormalizedDocument(BaseModel):
    """Source-independent document representation used by indexing layers."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID = Field(default_factory=uuid4)
    workspace_id: str = Field(min_length=1)
    source_id: UUID
    source_type: SourceType
    title: str | None = None
    content: str = Field(min_length=1)
    source_uri: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    row_id: str | None = None
    table_name: str | None = None
    section: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    """A retriever result with sufficient metadata to create a citation."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: UUID = Field(default_factory=uuid4)
    retriever: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_id: UUID
    source_type: SourceType
    raw_score: float | None = None
    normalized_score: float | None = Field(default=None, ge=0, le=1)
    source_uri: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    row_id: str | None = None
    table_name: str | None = None
    entity_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

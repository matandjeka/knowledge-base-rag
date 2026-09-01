"""Knowledge-source models and lifecycle values."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class SourceType(StrEnum):
    """Supported enterprise knowledge-source categories."""

    PDF = "pdf"
    WEBSITE = "website"
    CSV = "csv"
    DATABASE = "database"


class SourceStatus(StrEnum):
    """Lifecycle state of a registered source."""

    REGISTERED = "registered"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class SourceConfig(BaseModel):
    """Connector configuration safe to pass into an ingestion workflow."""

    source_type: SourceType
    uri: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class Source(BaseModel):
    """Registered knowledge source and its current lifecycle state."""

    source_id: UUID = Field(default_factory=uuid4)
    workspace_id: str
    name: str
    config: SourceConfig
    status: SourceStatus = SourceStatus.REGISTERED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

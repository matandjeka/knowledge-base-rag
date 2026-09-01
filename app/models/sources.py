"""Knowledge-source models and lifecycle values."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


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

    model_config = ConfigDict(extra="forbid")

    source_type: SourceType
    uri: str | None = Field(default=None, min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)


class Source(BaseModel):
    """Registered knowledge source and its current lifecycle state."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_id: UUID = Field(default_factory=uuid4)
    workspace_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    config: SourceConfig
    status: SourceStatus = SourceStatus.REGISTERED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SourceUpdate(BaseModel):
    """Mutable source metadata accepted by a registry update operation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1)
    config: SourceConfig | None = None

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> Self:
        """Distinguish omitted fields from invalid attempts to clear required metadata."""
        null_fields = {field for field in self.model_fields_set if getattr(self, field) is None}
        if null_fields:
            names = ", ".join(sorted(null_fields))
            raise ValueError(f"Source update fields cannot be null: {names}")
        return self

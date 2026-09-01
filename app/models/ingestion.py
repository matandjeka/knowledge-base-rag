"""Contracts returned by source-ingestion workflows."""

from pydantic import BaseModel, ConfigDict, Field

from app.models.sources import Source


class PdfIngestionResult(BaseModel):
    """Summary of a completed PDF ingestion operation."""

    model_config = ConfigDict(extra="forbid")

    source: Source
    page_count: int = Field(ge=1)
    chunk_count: int = Field(ge=1)

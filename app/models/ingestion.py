"""Contracts returned by source-ingestion workflows."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.sources import Source


class PdfIngestionResult(BaseModel):
    """Summary of a completed PDF ingestion operation."""

    model_config = ConfigDict(extra="forbid")

    source: Source
    page_count: int = Field(ge=1)
    chunk_count: int = Field(ge=1)


class WebsiteIngestionRequest(BaseModel):
    """User-controlled options for a bounded website crawl."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workspace_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    url: str = Field(min_length=1, max_length=2048)
    crawl_same_domain: bool = False
    page_limit: int = Field(default=20, ge=1, le=100)


class CrawlFailure(BaseModel):
    """One non-fatal page failure recorded during a website crawl."""

    model_config = ConfigDict(extra="forbid")

    url: str
    reason: str


class CrawlManifest(BaseModel):
    """Persisted diagnostics for a completed bounded crawl."""

    model_config = ConfigDict(extra="forbid")

    seed_url: str
    fetched_urls: list[str] = Field(default_factory=list)
    indexed_urls: list[str] = Field(default_factory=list)
    failures: list[CrawlFailure] = Field(default_factory=list)


class WebsiteIngestionResult(BaseModel):
    """Summary of a completed website ingestion operation."""

    model_config = ConfigDict(extra="forbid")

    source: Source
    page_count: int = Field(ge=1)
    chunk_count: int = Field(ge=1)
    skipped_count: int = Field(ge=0)


class CsvColumnType(StrEnum):
    """Conservative display types inferred for CSV preview columns."""

    BOOLEAN = "boolean"
    INTEGER = "integer"
    DECIMAL = "decimal"
    DATE = "date"
    DATETIME = "datetime"
    TEXT = "text"


class CsvColumnPreview(BaseModel):
    """One validated CSV column and its preview-only inferred type."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    inferred_type: CsvColumnType


class CsvPreviewResult(BaseModel):
    """Validated schema and bounded sample returned before CSV ingestion."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1)
    row_count: int = Field(ge=1)
    columns: list[CsvColumnPreview] = Field(min_length=1)
    sample_rows: list[dict[str, str | None]]


class CsvIngestionResult(BaseModel):
    """Summary of a completed CSV ingestion operation."""

    model_config = ConfigDict(extra="forbid")

    source: Source
    row_count: int = Field(ge=1)
    document_count: int = Field(ge=1)
    skipped_count: int = Field(ge=0)

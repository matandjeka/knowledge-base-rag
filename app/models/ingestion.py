"""Contracts returned by source-ingestion workflows."""

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

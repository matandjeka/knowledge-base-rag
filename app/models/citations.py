"""Typed citation locators and validated answer-segment contracts."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.sources import SourceType


class PdfCitationLocator(BaseModel):
    """Location within an indexed PDF."""

    model_config = ConfigDict(extra="forbid")

    source_type: Literal[SourceType.PDF] = SourceType.PDF
    page_number: int = Field(ge=1)


class WebsiteCitationLocator(BaseModel):
    """Original web page supporting a citation."""

    model_config = ConfigDict(extra="forbid")

    source_type: Literal[SourceType.WEBSITE] = SourceType.WEBSITE
    url: AnyHttpUrl


class CsvCitationLocator(BaseModel):
    """Stable row identity within an indexed CSV."""

    model_config = ConfigDict(extra="forbid")

    source_type: Literal[SourceType.CSV] = SourceType.CSV
    row_id: str = Field(min_length=1)


class DatabaseCitationLocator(BaseModel):
    """Approved table result or record supporting a database citation."""

    model_config = ConfigDict(extra="forbid")

    source_type: Literal[SourceType.DATABASE] = SourceType.DATABASE
    table_name: str = Field(min_length=1)
    row_id: str | None = Field(default=None, min_length=1)
    query_fingerprint: str | None = Field(default=None, min_length=1, max_length=128)


CitationLocator = Annotated[
    PdfCitationLocator | WebsiteCitationLocator | CsvCitationLocator | DatabaseCitationLocator,
    Field(discriminator="source_type"),
]
CitationId = Annotated[str, StringConstraints(pattern=r"^S[1-9][0-9]*$")]


class Citation(BaseModel):
    """Request-local source reference with typed inspection details."""

    model_config = ConfigDict(extra="forbid")

    citation_id: str = Field(pattern=r"^S[1-9][0-9]*$")
    evidence_id: UUID
    source_id: UUID
    source_type: SourceType
    source_title: str | None = None
    excerpt: str = Field(min_length=1, max_length=4000)
    locator: str = Field(min_length=1)
    locator_details: CitationLocator
    score: float
    retriever: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_locator_type(self) -> "Citation":
        """Prevent display identity from disagreeing with typed source identity."""
        if self.locator_details.source_type is not self.source_type:
            raise ValueError("Citation locator source type must match citation source type")
        return self


class AnswerCitationSegment(BaseModel):
    """One non-empty answer paragraph and its validated citation references."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    citation_ids: list[CitationId] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_citation_ids(self) -> Self:
        """Keep claim-to-citation relationships canonical and unambiguous."""
        if len(self.citation_ids) != len(set(self.citation_ids)):
            raise ValueError("Answer segment citation identifiers must be unique")
        return self

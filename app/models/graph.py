"""Canonical property-graph, extraction, and indexing models."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.sources import SourceType


class EntityType(StrEnum):
    PERSON = "PERSON"
    DEPARTMENT = "DEPARTMENT"
    POLICY = "POLICY"
    PRODUCT = "PRODUCT"
    PROJECT = "PROJECT"
    CONTRACT = "CONTRACT"
    REGULATION = "REGULATION"
    LOCATION = "LOCATION"
    ORGANIZATION = "ORGANIZATION"
    OTHER = "OTHER"


class RelationshipType(StrEnum):
    WORKS_FOR = "WORKS_FOR"
    OWNS = "OWNS"
    MANAGES = "MANAGES"
    APPLIES_TO = "APPLIES_TO"
    REFERENCES = "REFERENCES"
    GOVERNS = "GOVERNS"
    LOCATED_IN = "LOCATED_IN"
    PART_OF = "PART_OF"
    REQUIRES = "REQUIRES"
    RELATED_TO = "RELATED_TO"


class GraphSupport(BaseModel):
    """One source span supporting an entity mention or relationship."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    source_id: UUID
    source_type: SourceType
    text: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    source_uri: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    row_id: str | None = None
    table_name: str | None = None
    title: str | None = None

    @model_validator(mode="after")
    def validate_span(self) -> "GraphSupport":
        if self.end <= self.start:
            raise ValueError("Support span end must be greater than start")
        return self


class GraphEntity(BaseModel):
    """A canonical workspace entity with aliases and source mentions."""

    model_config = ConfigDict(extra="forbid")

    entity_id: UUID
    entity_type: EntityType
    canonical_name: str = Field(min_length=1)
    normalized_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    mentions: list[GraphSupport] = Field(min_length=1)


class GraphRelationship(BaseModel):
    """A typed directed edge with complete supporting provenance."""

    model_config = ConfigDict(extra="forbid")

    relationship_id: UUID
    subject_id: UUID
    predicate: RelationshipType
    object_id: UUID
    confidence: float = Field(ge=0, le=1)
    supports: list[GraphSupport] = Field(min_length=1)


class GraphPathSupport(BaseModel):
    """Associate one retrieved path edge with one supporting source span."""

    model_config = ConfigDict(extra="forbid")

    relationship_id: UUID
    support: GraphSupport


class GraphSnapshot(BaseModel):
    """One validated immutable workspace graph generation."""

    model_config = ConfigDict(extra="forbid")

    generation_id: UUID
    workspace_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    extractor_model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    source_ids: list[UUID] = Field(min_length=1)
    document_count: int = Field(ge=1)
    entities: list[GraphEntity] = Field(min_length=1)
    relationships: list[GraphRelationship] = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_graph_integrity(self) -> "GraphSnapshot":
        """Reject duplicate IDs, dangling edges, self-edges, and foreign provenance."""
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("Graph source identifiers must be unique")
        entities = {entity.entity_id for entity in self.entities}
        if len(entities) != len(self.entities):
            raise ValueError("Graph entity identifiers must be unique")
        relationships = {relationship.relationship_id for relationship in self.relationships}
        if len(relationships) != len(self.relationships):
            raise ValueError("Graph relationship identifiers must be unique")
        sources = set(self.source_ids)
        for entity in self.entities:
            if any(mention.source_id not in sources for mention in entity.mentions):
                raise ValueError("Graph entity mention references an unknown source")
        for relationship in self.relationships:
            if relationship.subject_id not in entities or relationship.object_id not in entities:
                raise ValueError("Graph relationship contains a dangling endpoint")
            if relationship.subject_id == relationship.object_id:
                raise ValueError("Graph relationships cannot be self-referential")
            if any(support.source_id not in sources for support in relationship.supports):
                raise ValueError("Graph relationship support references an unknown source")
        return self


class GraphGenerationMetadata(BaseModel):
    """Compatibility and integrity metadata for a persisted graph generation."""

    model_config = ConfigDict(extra="forbid")

    generation_id: UUID
    schema_version: str
    extractor_model: str
    prompt_version: str
    source_count: int = Field(ge=1)
    document_count: int = Field(ge=1)
    entity_count: int = Field(ge=1)
    relationship_count: int = Field(ge=1)
    graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class GraphIndexRequest(BaseModel):
    """Request to rebuild one workspace graph explicitly."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workspace_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class GraphIndexResult(BaseModel):
    """Summary of one activated workspace graph generation."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    generation_id: UUID
    source_count: int = Field(ge=1)
    document_count: int = Field(ge=1)
    entity_count: int = Field(ge=1)
    relationship_count: int = Field(ge=1)


class ExtractedEntity(BaseModel):
    """Provider output for one entity mention in a document."""

    model_config = ConfigDict(extra="forbid")

    reference: str = Field(min_length=1)
    entity_type: EntityType
    canonical_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    supporting_text: str = Field(min_length=1)


class ExtractedRelationship(BaseModel):
    """Provider output for one supported relationship between extracted entities."""

    model_config = ConfigDict(extra="forbid")

    subject_reference: str = Field(min_length=1)
    predicate: RelationshipType
    object_reference: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    supporting_text: str = Field(min_length=1)


class DocumentGraphExtraction(BaseModel):
    """Strict structured extraction result for one normalized document."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    entities: list[ExtractedEntity]
    relationships: list[ExtractedRelationship]


class BatchGraphExtraction(BaseModel):
    """Strict provider response for one bounded document batch."""

    model_config = ConfigDict(extra="forbid")

    documents: list[DocumentGraphExtraction]

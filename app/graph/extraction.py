"""Graph extraction contracts and deterministic workspace entity resolution."""

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from app.core.exceptions import IndexingError
from app.models import (
    DocumentGraphExtraction,
    EntityType,
    GraphEntity,
    GraphRelationship,
    GraphSupport,
    NormalizedDocument,
    RelationshipType,
)

GRAPH_SCHEMA_VERSION = "1"
GRAPH_PROMPT_VERSION = "1"


class GraphExtractor(Protocol):
    """Extract typed entities and supported relationships from normalized documents."""

    @property
    def model_name(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    async def extract(
        self, documents: Sequence[NormalizedDocument]
    ) -> list[DocumentGraphExtraction]: ...


@dataclass(frozen=True, slots=True)
class _MentionRecord:
    document: NormalizedDocument
    reference: str
    entity_type: EntityType
    canonical_name: str
    aliases: tuple[str, ...]
    support: GraphSupport


def resolve_graph(
    workspace_id: str,
    documents: Sequence[NormalizedDocument],
    extractions: Sequence[DocumentGraphExtraction],
) -> tuple[list[GraphEntity], list[GraphRelationship]]:
    """Resolve document extractions into deterministic entities and aggregated edges."""
    document_map = {document.document_id: document for document in documents}
    if len(extractions) != len(document_map) or {
        extraction.document_id for extraction in extractions
    } != set(document_map):
        raise IndexingError("Graph extraction results do not match the indexed documents")

    mentions: list[_MentionRecord] = []
    references: dict[tuple[UUID, str], int] = {}
    for extraction in sorted(extractions, key=lambda item: str(item.document_id)):
        document = document_map[extraction.document_id]
        for entity in extraction.entities:
            reference_key = (document.document_id, entity.reference)
            if reference_key in references:
                raise IndexingError("Graph extraction contains a duplicate entity reference")
            support = _support(document, entity.supporting_text)
            references[reference_key] = len(mentions)
            mentions.append(
                _MentionRecord(
                    document=document,
                    reference=entity.reference,
                    entity_type=entity.entity_type,
                    canonical_name=entity.canonical_name.strip(),
                    aliases=tuple(alias.strip() for alias in entity.aliases if alias.strip()),
                    support=support,
                )
            )

    parents = list(range(len(mentions)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    names: dict[tuple[EntityType, str], int] = {}
    for index, mention in enumerate(mentions):
        for name in (mention.canonical_name, *mention.aliases):
            name_key = (mention.entity_type, normalize_entity_name(name))
            if not name_key[1]:
                continue
            existing = names.get(name_key)
            if existing is None:
                names[name_key] = index
            else:
                union(index, existing)

    groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(mentions)):
        groups[find(index)].append(index)

    entities: list[GraphEntity] = []
    mention_to_entity: dict[int, UUID] = {}
    for indexes in groups.values():
        records = [mentions[index] for index in indexes]
        entity_type = records[0].entity_type
        canonical = min(
            (record.canonical_name for record in records),
            key=lambda value: (len(value), value.casefold(), value),
        )
        normalized = normalize_entity_name(canonical)
        entity_id = uuid5(NAMESPACE_URL, f"{workspace_id}:{entity_type.value}:{normalized}")
        aliases = sorted(
            {
                name
                for record in records
                for name in (record.canonical_name, *record.aliases)
                if normalize_entity_name(name) != normalized
            },
            key=lambda value: (value.casefold(), value),
        )
        supports = _unique_supports(record.support for record in records)
        entities.append(
            GraphEntity(
                entity_id=entity_id,
                entity_type=entity_type,
                canonical_name=canonical,
                normalized_name=normalized,
                aliases=aliases,
                mentions=supports,
            )
        )
        for index in indexes:
            mention_to_entity[index] = entity_id

    relationship_groups: dict[
        tuple[UUID, RelationshipType, UUID], list[tuple[float, GraphSupport]]
    ] = defaultdict(list)
    for extraction in extractions:
        document = document_map[extraction.document_id]
        for relationship in extraction.relationships:
            try:
                subject = mention_to_entity[
                    references[(document.document_id, relationship.subject_reference)]
                ]
                object_ = mention_to_entity[
                    references[(document.document_id, relationship.object_reference)]
                ]
            except KeyError as error:
                raise IndexingError("Graph relationship references an unknown entity") from error
            relationship_key = (subject, relationship.predicate, object_)
            relationship_groups[relationship_key].append(
                (relationship.confidence, _support(document, relationship.supporting_text))
            )

    relationships = [
        GraphRelationship(
            relationship_id=uuid5(NAMESPACE_URL, f"{workspace_id}:{subject}:{predicate}:{object_}"),
            subject_id=subject,
            predicate=predicate,
            object_id=object_,
            confidence=max(confidence for confidence, _ in values),
            supports=_unique_supports(support for _, support in values),
        )
        for (subject, predicate, object_), values in relationship_groups.items()
    ]
    if not entities or not relationships:
        raise IndexingError("Graph extraction produced no connected knowledge")
    return (
        sorted(entities, key=lambda entity: str(entity.entity_id)),
        sorted(relationships, key=lambda relationship: str(relationship.relationship_id)),
    )


def normalize_entity_name(value: str) -> str:
    """Normalize a name conservatively for exact entity and alias matching."""
    normalized = re.sub(r"[^\w\s]", " ", value.casefold())
    words = normalized.split()
    suffixes = {"incorporated": "inc", "corporation": "corp", "limited": "ltd"}
    return " ".join(suffixes.get(word, word) for word in words)


def _support(document: NormalizedDocument, text: str) -> GraphSupport:
    supporting_text = text.strip()
    start = document.content.find(supporting_text)
    if start < 0:
        raise IndexingError("Graph support text is not present in its source document")
    return GraphSupport(
        document_id=document.document_id,
        source_id=document.source_id,
        source_type=document.source_type,
        text=supporting_text,
        start=start,
        end=start + len(supporting_text),
        source_uri=document.source_uri,
        page_number=document.page_number,
        row_id=document.row_id,
        table_name=document.table_name,
        title=document.title,
    )


def _unique_supports(supports: Iterable[GraphSupport]) -> list[GraphSupport]:
    unique: dict[tuple[UUID, int, int], GraphSupport] = {}
    for support in supports:
        unique[(support.document_id, support.start, support.end)] = support
    keys = sorted(unique, key=lambda item: (str(item[0]), item[1], item[2]))
    return [unique[key] for key in keys]

"""Deterministic entity anchoring and bounded property-graph traversal."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.graph.extraction import normalize_entity_name
from app.graph.store import GraphStore
from app.models import Evidence, GraphEntity, GraphPathSupport, GraphRelationship, GraphSnapshot

_PREDICATE_TERMS = {
    "WORKS_FOR": {"work", "works", "employed", "employment"},
    "OWNS": {"own", "owns", "owner", "owned"},
    "MANAGES": {"manage", "manages", "manager", "managed"},
    "APPLIES_TO": {"apply", "applies", "applicable"},
    "REFERENCES": {"reference", "references", "cites"},
    "GOVERNS": {"govern", "governs", "governed"},
    "LOCATED_IN": {"located", "location", "where"},
    "PART_OF": {"part", "belongs"},
    "REQUIRES": {"require", "requires", "required"},
    "RELATED_TO": {"related", "relationship"},
}


@dataclass(frozen=True, slots=True)
class _GraphPath:
    nodes: tuple[UUID, ...]
    edges: tuple[GraphRelationship, ...]


class GraphRetriever:
    """Retrieve ranked one- and two-hop graph paths from named query anchors."""

    def __init__(self, graph_store: GraphStore, *, max_hops: int = 2) -> None:
        self._graph_store = graph_store
        self._max_hops = max_hops

    async def retrieve(
        self,
        workspace_id: str,
        query: str,
        *,
        top_k: int,
        source_ids: frozenset[UUID],
        min_similarity: float,
    ) -> list[Evidence]:
        snapshot = await self._graph_store.load(workspace_id)
        normalized_query = normalize_entity_name(query)
        query_terms = set(normalized_query.split())
        entities = {entity.entity_id: entity for entity in snapshot.entities}
        anchors = [
            entity for entity in snapshot.entities if _anchor_quality(entity, normalized_query) > 0
        ]
        if not anchors:
            return []
        paths = _walk_paths(snapshot, anchors, self._max_hops, source_ids)
        scored = sorted(
            ((_path_score(path, entities, normalized_query, query_terms), path) for path in paths),
            key=lambda item: (
                -item[0],
                tuple(str(edge.relationship_id) for edge in item[1].edges),
            ),
        )
        evidence: list[Evidence] = []
        seen: set[tuple[UUID, ...]] = set()
        for score, path in scored:
            if score < min_similarity:
                continue
            identity = tuple(edge.relationship_id for edge in path.edges)
            if identity in seen:
                continue
            seen.add(identity)
            supports = _path_supports(path, source_ids)
            if not supports:
                continue
            primary = supports[0].support
            evidence.append(
                Evidence(
                    retriever="graph",
                    content=_render_path(path, entities),
                    source_id=primary.source_id,
                    source_type=primary.source_type,
                    raw_score=score,
                    normalized_score=score,
                    source_uri=primary.source_uri,
                    page_number=primary.page_number,
                    row_id=primary.row_id,
                    table_name=primary.table_name,
                    entity_ids=_path_entity_ids(path),
                    metadata={
                        "title": primary.title,
                        "generation_id": str(snapshot.generation_id),
                        "graph_path": [
                            {
                                "relationship_id": str(edge.relationship_id),
                                "subject_id": str(edge.subject_id),
                                "predicate": edge.predicate.value,
                                "object_id": str(edge.object_id),
                                "confidence": edge.confidence,
                            }
                            for edge in path.edges
                        ],
                        "citation_supports": [
                            support.model_dump(mode="json") for support in supports
                        ],
                    },
                )
            )
            if len(evidence) == top_k:
                break
        return evidence


def _walk_paths(
    snapshot: GraphSnapshot,
    anchors: Sequence[GraphEntity],
    max_hops: int,
    source_ids: frozenset[UUID],
) -> list[_GraphPath]:
    adjacency: dict[UUID, list[GraphRelationship]] = defaultdict(list)
    for relationship in snapshot.relationships:
        if source_ids and not any(
            support.source_id in source_ids for support in relationship.supports
        ):
            continue
        adjacency[relationship.subject_id].append(relationship)
        adjacency[relationship.object_id].append(relationship)
    paths: list[_GraphPath] = []
    for anchor in anchors:
        stack: list[
            tuple[UUID, tuple[GraphRelationship, ...], tuple[UUID, ...], frozenset[UUID]]
        ] = [(anchor.entity_id, (), (anchor.entity_id,), frozenset({anchor.entity_id}))]
        while stack:
            current, edges, nodes, visited = stack.pop()
            if edges:
                paths.append(_GraphPath(nodes=nodes, edges=edges))
            if len(edges) == max_hops:
                continue
            for edge in adjacency[current]:
                if edge in edges:
                    continue
                neighbor = edge.object_id if edge.subject_id == current else edge.subject_id
                if neighbor in visited:
                    continue
                stack.append((neighbor, (*edges, edge), (*nodes, neighbor), visited | {neighbor}))
    return paths


def _anchor_quality(entity: GraphEntity, normalized_query: str) -> float:
    names = [entity.normalized_name, *(normalize_entity_name(alias) for alias in entity.aliases)]
    return max(
        (
            len(name.split()) / max(1, len(normalized_query.split()))
            for name in names
            if _contains_name(normalized_query, name)
        ),
        default=0.0,
    )


def _contains_name(query: str, name: str) -> bool:
    return bool(name) and f" {name} " in f" {query} "


def _path_score(
    path: _GraphPath,
    entities: dict[UUID, GraphEntity],
    normalized_query: str,
    query_terms: set[str],
) -> float:
    anchor = _anchor_quality(entities[path.nodes[0]], normalized_query)
    predicate_terms = {
        term for edge in path.edges for term in _PREDICATE_TERMS.get(edge.predicate.value, set())
    }
    predicate_overlap = float(bool(predicate_terms & query_terms))
    confidence = sum(edge.confidence for edge in path.edges) / len(path.edges)
    support = min(1.0, sum(len(edge.supports) for edge in path.edges) / (2 * len(path.edges)))
    length = 1.0 if len(path.edges) == 1 else 0.9
    return min(
        1.0,
        0.30 * anchor
        + 0.25 * predicate_overlap
        + 0.25 * confidence
        + 0.10 * support
        + 0.10 * length,
    )


def _path_supports(path: _GraphPath, source_ids: frozenset[UUID]) -> list[GraphPathSupport]:
    supports: list[GraphPathSupport] = []
    seen: set[tuple[UUID, UUID, int, int]] = set()
    edges = sorted(path.edges, key=lambda item: (-item.confidence, str(item.relationship_id)))
    for edge in edges:
        edge_supports = sorted(
            edge.supports,
            key=lambda item: (str(item.source_id), str(item.document_id), item.start),
        )
        for support in edge_supports:
            if source_ids and support.source_id not in source_ids:
                continue
            key = (edge.relationship_id, support.document_id, support.start, support.end)
            if key in seen:
                continue
            seen.add(key)
            supports.append(GraphPathSupport(relationship_id=edge.relationship_id, support=support))
    return supports


def _render_path(path: _GraphPath, entities: dict[UUID, GraphEntity]) -> str:
    parts = [entities[path.nodes[0]].canonical_name]
    for position, edge in enumerate(path.edges):
        current = path.nodes[position]
        neighbor = path.nodes[position + 1]
        if edge.subject_id == current and edge.object_id == neighbor:
            parts.extend((f"— {edge.predicate.value} →", entities[neighbor].canonical_name))
        else:
            parts.extend((f"← {edge.predicate.value} —", entities[neighbor].canonical_name))
    return " ".join(parts)


def _path_entity_ids(path: _GraphPath) -> list[str]:
    return [str(identifier) for identifier in path.nodes]

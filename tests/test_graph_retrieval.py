"""Phase 7 graph resolution, persistence, retrieval, provenance, and benchmark tests."""

from pathlib import Path
from time import perf_counter
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter

from app.citations.builder import build_citations
from app.core.exceptions import GraphIndexNotFoundError, IndexingError
from app.evaluation.graph_comparison import GraphBenchmarkCase, GraphBenchmarkRun, score_graph_runs
from app.graph.extraction import normalize_entity_name, resolve_graph
from app.graph.retrieval import GraphRetriever
from app.graph.store import LocalGraphStore
from app.models import (
    DocumentGraphExtraction,
    EntityType,
    ExtractedEntity,
    ExtractedRelationship,
    GraphEntity,
    GraphRelationship,
    GraphSnapshot,
    GraphSupport,
    NormalizedDocument,
    RelationshipType,
    SourceType,
)


class GraphFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor: str
    middle: str
    target: str
    question: str
    predicate: RelationshipType


def _support(
    document_id: UUID,
    source_id: UUID,
    source_type: SourceType,
    text: str,
    locator_number: int,
) -> GraphSupport:
    values: dict[str, object] = {}
    if source_type is SourceType.PDF:
        values["page_number"] = locator_number
    elif source_type is SourceType.WEBSITE:
        values["source_uri"] = f"https://example.test/{locator_number}"
    else:
        values["row_id"] = f"row-{locator_number}"
    return GraphSupport(
        document_id=document_id,
        source_id=source_id,
        source_type=source_type,
        text=text,
        start=0,
        end=len(text),
        title="benchmark source",
        **values,
    )


def _entity(workspace: str, name: str, entity_type: EntityType, source_id: UUID) -> GraphEntity:
    normalized = normalize_entity_name(name)
    identifier = uuid5(NAMESPACE_URL, f"{workspace}:{entity_type.value}:{normalized}")
    mention = _support(uuid4(), source_id, SourceType.PDF, name, 1)
    return GraphEntity(
        entity_id=identifier,
        entity_type=entity_type,
        canonical_name=name,
        normalized_name=normalized,
        mentions=[mention],
    )


def test_resolution_merges_aliases_and_aggregates_relationship_support() -> None:
    workspace = "workspace"
    source_id = uuid4()
    first = NormalizedDocument(
        workspace_id=workspace,
        source_id=source_id,
        source_type=SourceType.PDF,
        content="Luthan Group owns Project Atlas.",
        page_number=1,
    )
    second = NormalizedDocument(
        workspace_id=workspace,
        source_id=source_id,
        source_type=SourceType.PDF,
        content="Luthan Group Incorporated owns Project Atlas.",
        page_number=2,
    )
    extractions = [
        DocumentGraphExtraction(
            document_id=document.document_id,
            entities=[
                ExtractedEntity(
                    reference="org",
                    entity_type=EntityType.ORGANIZATION,
                    canonical_name=name,
                    aliases=["Luthan Group Inc."],
                    supporting_text=name,
                ),
                ExtractedEntity(
                    reference="project",
                    entity_type=EntityType.PROJECT,
                    canonical_name="Project Atlas",
                    supporting_text="Project Atlas",
                ),
            ],
            relationships=[
                ExtractedRelationship(
                    subject_reference="org",
                    predicate=RelationshipType.OWNS,
                    object_reference="project",
                    confidence=0.9,
                    supporting_text=document.content,
                )
            ],
        )
        for document, name in (
            (first, "Luthan Group"),
            (second, "Luthan Group Incorporated"),
        )
    ]

    entities, relationships = resolve_graph(workspace, [first, second], extractions)

    assert len(entities) == 2
    assert len(relationships) == 1
    assert len(relationships[0].supports) == 2
    assert relationships[0].relationship_id == uuid5(
        NAMESPACE_URL,
        f"{workspace}:{relationships[0].subject_id}:OWNS:{relationships[0].object_id}",
    )


@pytest.mark.asyncio
async def test_local_graph_generation_requires_activation_and_detects_corruption(
    tmp_path: Path,
) -> None:
    snapshot, _, _ = _benchmark_snapshot()
    store = LocalGraphStore(tmp_path)
    metadata = await store.prepare(snapshot)
    with pytest.raises(GraphIndexNotFoundError):
        await store.load(snapshot.workspace_id)
    await store.activate(snapshot.workspace_id, metadata.generation_id)
    assert (await store.load(snapshot.workspace_id)).generation_id == snapshot.generation_id
    graph_path = (
        tmp_path
        / "graph-indexes"
        / "workspaces"
        / snapshot.workspace_id
        / "generations"
        / str(snapshot.generation_id)
        / "graph.json"
    )
    graph_path.write_text("{}")
    with pytest.raises(IndexingError, match=r"checksum|unreadable"):
        await store.load(snapshot.workspace_id)


@pytest.mark.asyncio
async def test_local_graph_store_rejects_incompatible_schema_before_activation(
    tmp_path: Path,
) -> None:
    snapshot, _, _ = _benchmark_snapshot()
    incompatible = snapshot.model_copy(update={"schema_version": "legacy"})
    store = LocalGraphStore(tmp_path)
    metadata = await store.prepare(incompatible)

    with pytest.raises(IndexingError, match="incompatible"):
        await store.activate(incompatible.workspace_id, metadata.generation_id)

    model_store = LocalGraphStore(tmp_path / "model", expected_extractor_model="new-model")
    model_metadata = await model_store.prepare(snapshot)
    with pytest.raises(IndexingError, match="model changed"):
        await model_store.activate(snapshot.workspace_id, model_metadata.generation_id)


@pytest.mark.asyncio
async def test_graph_retrieval_returns_two_hop_edge_level_citations(tmp_path: Path) -> None:
    snapshot, fixtures, paths = _benchmark_snapshot()
    store = LocalGraphStore(tmp_path)
    metadata = await store.prepare(snapshot)
    await store.activate(snapshot.workspace_id, metadata.generation_id)
    retriever = GraphRetriever(store, max_hops=2)

    evidence = await retriever.retrieve(
        snapshot.workspace_id,
        fixtures[0].question,
        top_k=5,
        source_ids=frozenset(),
        min_similarity=0.7,
    )
    citations = build_citations(evidence)

    expected = tuple(str(identifier) for identifier in paths[0][1])
    match = next(
        item
        for item in evidence
        if tuple(edge["relationship_id"] for edge in item.metadata["graph_path"]) == expected
    )
    assert len(citations) >= 2
    assert set(match.metadata["edge_citation_ids"]) == set(expected)
    assert {
        citation.evidence_id for citation in citations if citation.evidence_id == match.evidence_id
    }

    unmatched = await retriever.retrieve(
        snapshot.workspace_id,
        "What governs an unknown initiative?",
        top_k=5,
        source_ids=frozenset(),
        min_similarity=0.7,
    )
    assert unmatched == []

    first_edge_source = snapshot.relationships[0].supports[0].source_id
    filtered = await retriever.retrieve(
        snapshot.workspace_id,
        fixtures[0].question,
        top_k=5,
        source_ids=frozenset({first_edge_source}),
        min_similarity=0,
    )
    assert filtered
    assert all(
        citation.source_id == first_edge_source
        for item in filtered
        for citation in build_citations([item])
    )


@pytest.mark.asyncio
async def test_five_case_multi_hop_benchmark_has_complete_paths_and_provenance(
    tmp_path: Path,
) -> None:
    snapshot, fixtures, paths = _benchmark_snapshot()
    store = LocalGraphStore(tmp_path)
    metadata = await store.prepare(snapshot)
    await store.activate(snapshot.workspace_id, metadata.generation_id)
    retriever = GraphRetriever(store, max_hops=2)
    runs: list[GraphBenchmarkRun] = []
    for fixture, (anchor_id, relationship_ids, locators) in zip(fixtures, paths, strict=True):
        started = perf_counter()
        evidence = await retriever.retrieve(
            snapshot.workspace_id,
            fixture.question,
            top_k=5,
            source_ids=frozenset(),
            min_similarity=0.7,
        )
        citations = build_citations(evidence)
        runs.append(
            GraphBenchmarkRun(
                case=GraphBenchmarkCase(
                    question=fixture.question,
                    anchor_entity_id=anchor_id,
                    expected_relationship_ids=relationship_ids,
                    expected_locators=locators,
                ),
                evidence=tuple(evidence),
                citations=tuple(citations),
                latency_seconds=perf_counter() - started,
            )
        )

    metrics = score_graph_runs(runs)

    assert metrics.case_count == 5
    assert metrics.path_hit_rate_at_k == 1
    assert metrics.path_mean_reciprocal_rank == 1
    assert metrics.entity_linking_accuracy == 1
    assert metrics.edge_citation_coverage == 1
    assert metrics.cross_source_citation_accuracy == 1
    assert metrics.mean_latency_seconds >= 0


def _benchmark_snapshot() -> tuple[
    GraphSnapshot,
    list[GraphFixture],
    list[tuple[UUID, tuple[UUID, UUID], frozenset[str]]],
]:
    workspace = "graph-benchmark"
    fixture_path = Path(__file__).parent / "fixtures" / "graph_benchmark.json"
    fixtures = TypeAdapter(list[GraphFixture]).validate_json(fixture_path.read_bytes())
    entities: list[GraphEntity] = []
    relationships: list[GraphRelationship] = []
    source_ids: list[UUID] = []
    paths: list[tuple[UUID, tuple[UUID, UUID], frozenset[str]]] = []
    source_types = [SourceType.PDF, SourceType.WEBSITE, SourceType.CSV]
    for index, fixture in enumerate(fixtures, start=1):
        first_source, second_source = uuid4(), uuid4()
        source_ids.extend((first_source, second_source))
        anchor = _entity(workspace, fixture.anchor, EntityType.PROJECT, first_source)
        middle = _entity(workspace, fixture.middle, EntityType.DEPARTMENT, first_source)
        target = _entity(workspace, fixture.target, EntityType.POLICY, second_source)
        entities.extend((anchor, middle, target))
        first_type = source_types[(index - 1) % len(source_types)]
        second_type = source_types[index % len(source_types)]
        first_support = _support(uuid4(), first_source, first_type, "Ownership fact.", index)
        second_support = _support(
            uuid4(), second_source, second_type, "Governance fact.", index + 10
        )
        first_id = uuid5(
            NAMESPACE_URL, f"{workspace}:{anchor.entity_id}:PART_OF:{middle.entity_id}"
        )
        second_id = uuid5(
            NAMESPACE_URL, f"{workspace}:{middle.entity_id}:GOVERNS:{target.entity_id}"
        )
        relationships.extend(
            (
                GraphRelationship(
                    relationship_id=first_id,
                    subject_id=anchor.entity_id,
                    predicate=RelationshipType.PART_OF,
                    object_id=middle.entity_id,
                    confidence=1,
                    supports=[first_support],
                ),
                GraphRelationship(
                    relationship_id=second_id,
                    subject_id=middle.entity_id,
                    predicate=fixture.predicate,
                    object_id=target.entity_id,
                    confidence=1,
                    supports=[second_support],
                ),
            )
        )
        paths.append(
            (
                anchor.entity_id,
                (first_id, second_id),
                frozenset((_locator(first_support), _locator(second_support))),
            )
        )
    snapshot = GraphSnapshot(
        generation_id=uuid4(),
        workspace_id=workspace,
        schema_version="1",
        extractor_model="deterministic-test",
        prompt_version="1",
        source_ids=source_ids,
        document_count=len(source_ids),
        entities=entities,
        relationships=relationships,
    )
    return snapshot, fixtures, paths


def _locator(support: GraphSupport) -> str:
    if support.page_number:
        return f"page {support.page_number}"
    if support.source_uri:
        return support.source_uri
    assert support.row_id
    return f"row {support.row_id}"

"""Phase 13 typed locator, inline marker, and citation identity tests."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import get_query_service
from app.citations import build_citations, validate_inline_citations
from app.core.exceptions import GenerationError
from app.generation.extractive import GeneratedAnswer
from app.main import app
from app.models import (
    AnswerCitationSegment,
    Citation,
    Evidence,
    GraphPathSupport,
    GraphSupport,
    PdfCitationLocator,
    QueryRequest,
    QueryResponse,
    SourceType,
    WebsiteCitationLocator,
)
from app.repositories import InMemorySourceRepository
from app.retrieval.query_service import QueryService


class CitationFixture(BaseModel):
    """Expected typed locator produced for one source category."""

    model_config = ConfigDict(extra="forbid")

    key: str
    source_type: SourceType
    content: str
    expected_locator: str
    source_uri: str | None = None
    page_number: int | None = None
    row_id: str | None = None
    table_name: str | None = None
    query_fingerprint: str | None = None


def _evidence(**overrides: Any) -> Evidence:
    values: dict[str, Any] = {
        "retriever": "vector",
        "content": "Supported fact.",
        "source_id": uuid4(),
        "source_type": SourceType.PDF,
        "page_number": 1,
        "raw_score": 0.9,
        "metadata": {"title": "Policy"},
    }
    values.update(overrides)
    return Evidence(**values)


def test_committed_locator_benchmark_has_complete_source_accuracy() -> None:
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "citation_benchmark.json").read_text()
    )
    fixtures = [CitationFixture.model_validate(item) for item in payload]
    results: list[bool] = []
    for fixture in fixtures:
        metadata = (
            {"query_fingerprint": fixture.query_fingerprint} if fixture.query_fingerprint else {}
        )
        evidence = _evidence(
            content=fixture.content,
            source_type=fixture.source_type,
            source_uri=fixture.source_uri,
            page_number=fixture.page_number,
            row_id=fixture.row_id,
            table_name=fixture.table_name,
            metadata=metadata,
        )
        citation = build_citations([evidence])[0]
        results.append(
            citation.locator == fixture.expected_locator
            and citation.locator_details.source_type is fixture.source_type
        )

    assert len(fixtures) == 4
    assert all(results)


def test_canonical_identity_deduplicates_exact_support_but_not_distinct_passages() -> None:
    source_id = uuid4()
    duplicate_a = _evidence(source_id=source_id, content="Same fact.", page_number=3)
    duplicate_b = _evidence(source_id=source_id, content=" Same   fact. ", page_number=3)
    distinct = _evidence(source_id=source_id, content="Different fact.", page_number=3)

    citations = build_citations([duplicate_a, duplicate_b, distinct])

    assert [citation.citation_id for citation in citations] == ["S1", "S2"]
    assert duplicate_a.metadata["citation_ids"] == ["S1"]
    assert duplicate_b.metadata["citation_ids"] == ["S1"]
    assert distinct.metadata["citation_ids"] == ["S2"]


def test_graph_edges_preserve_mapping_when_they_share_exact_support() -> None:
    source_id = uuid4()
    support = GraphSupport(
        document_id=uuid4(),
        source_id=source_id,
        source_type=SourceType.PDF,
        text="Atlas is governed by HR-402.",
        start=0,
        end=28,
        page_number=8,
    )
    first_edge, second_edge = uuid4(), uuid4()
    graph = _evidence(
        retriever="graph",
        source_id=source_id,
        content="Atlas relationship path",
        page_number=8,
        metadata={
            "citation_supports": [
                GraphPathSupport(relationship_id=edge, support=support).model_dump(mode="json")
                for edge in (first_edge, second_edge)
            ]
        },
    )

    citations = build_citations([graph])

    assert [citation.citation_id for citation in citations] == ["S1"]
    assert graph.metadata["edge_citation_ids"] == {
        str(first_edge): ["S1"],
        str(second_edge): ["S1"],
    }


def test_inline_validator_resolves_multi_citation_segments() -> None:
    citations = build_citations(
        [_evidence(content="First fact."), _evidence(content="Second fact.", page_number=2)]
    )

    segments = validate_inline_citations(
        "Combined answer. [S1] [S2]",
        citations,
        insufficient_evidence=False,
    )

    assert segments[0].text == "Combined answer."
    assert segments[0].citation_ids == ["S1", "S2"]


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ("Uncited answer.", "uncited factual segment"),
        ("Unknown source. [S9]", "unknown citation"),
        ("Malformed source. [S0]", "malformed citation marker"),
        ("[S1] Marker before the claim.", "misplaced citation marker"),
        ("First claim. [S1] Trailing uncited claim.", "misplaced citation marker"),
    ],
)
def test_inline_validator_rejects_invalid_generation(answer: str, message: str) -> None:
    citations = build_citations([_evidence()])

    with pytest.raises(GenerationError, match=message):
        validate_inline_citations(answer, citations, insufficient_evidence=False)


def test_insufficient_evidence_needs_no_inline_markers() -> None:
    assert (
        validate_inline_citations("There is not enough evidence.", [], insufficient_evidence=True)
        == []
    )


def test_answer_segment_contract_rejects_invalid_or_duplicate_ids() -> None:
    with pytest.raises(ValueError):
        AnswerCitationSegment(text="Fact", citation_ids=["source-1"])
    with pytest.raises(ValueError, match="must be unique"):
        AnswerCitationSegment(text="Fact", citation_ids=["S1", "S1"])


def test_website_locator_rejects_non_http_schemes() -> None:
    with pytest.raises(ValueError):
        WebsiteCitationLocator(url="javascript:alert(1)")


def test_citation_contract_rejects_a_mismatched_typed_locator() -> None:
    with pytest.raises(ValueError, match="source type must match"):
        Citation(
            citation_id="S1",
            evidence_id=uuid4(),
            source_id=uuid4(),
            source_type=SourceType.PDF,
            excerpt="Fact",
            locator="page 1",
            locator_details=WebsiteCitationLocator(url="https://example.test"),
            score=1,
            retriever="vector",
        )


@pytest.mark.asyncio
async def test_query_api_maps_invalid_generation_and_preserves_optional_fields() -> None:
    class StubQueryService:
        async def query(self, request: QueryRequest, **_kwargs: object) -> QueryResponse:
            if request.question == "invalid":
                raise GenerationError("Generated answer references an unknown citation")
            return QueryResponse(answer="No evidence", insufficient_evidence=True)

    app.dependency_overrides[get_query_service] = lambda: StubQueryService()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            invalid = await client.post(
                "/query", json={"workspace_id": "workspace", "question": "invalid"}
            )
            legacy = await client.post(
                "/query", json={"workspace_id": "workspace", "question": "legacy"}
            )
    finally:
        app.dependency_overrides.clear()

    assert invalid.status_code == 502
    assert legacy.status_code == 200
    assert "citation_segments" not in legacy.json()
    assert "routing_trace" not in legacy.json()


@pytest.mark.asyncio
async def test_query_api_serializes_populated_typed_citation_contract() -> None:
    citation = build_citations(
        [
            _evidence(
                source_type=SourceType.WEBSITE,
                source_uri="https://example.test/policy",
                page_number=None,
            )
        ]
    )[0]

    class StubQueryService:
        async def query(self, request: QueryRequest, **_kwargs: object) -> QueryResponse:
            del request
            return QueryResponse(
                answer="Supported answer. [S1]",
                citations=[citation],
                insufficient_evidence=False,
                citation_segments=[
                    AnswerCitationSegment(text="Supported answer.", citation_ids=["S1"])
                ],
            )

    app.dependency_overrides[get_query_service] = lambda: StubQueryService()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/query", json={"workspace_id": "workspace", "question": "question"}
            )
    finally:
        app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert payload["citation_segments"] == [{"text": "Supported answer.", "citation_ids": ["S1"]}]
    assert payload["citations"][0]["locator_details"] == {
        "source_type": "website",
        "url": "https://example.test/policy",
    }
    assert payload["citations"][0]["retriever"] == "vector"


@pytest.mark.asyncio
async def test_generator_can_decline_irrelevant_retrieved_evidence() -> None:
    evidence = _evidence()

    class StaticRetriever:
        async def retrieve(self, *args: Any, **kwargs: Any) -> list[Evidence]:
            del args, kwargs
            return [evidence]

    class DecliningGenerator:
        async def generate(self, *args: Any, **kwargs: Any) -> GeneratedAnswer:
            del args, kwargs
            return GeneratedAnswer(
                answer="I could not find enough evidence to answer reliably.",
                insufficient_evidence=True,
            )

    retriever = StaticRetriever()
    service = QueryService(
        InMemorySourceRepository(),
        retriever,
        retriever,
        DecliningGenerator(),
        default_top_k=5,
        max_top_k=20,
        min_similarity=0.7,
    )

    response = await service.query(QueryRequest(workspace_id="workspace", question="Question?"))

    assert response.insufficient_evidence
    assert response.evidence == [evidence]
    assert response.citation_segments == []


def test_pdf_locator_model_remains_strict() -> None:
    with pytest.raises(ValueError):
        PdfCitationLocator(page_number=0)

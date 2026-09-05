"""Request-local citation construction for canonical retrieval evidence."""

import re
from collections.abc import Hashable

from app.core.exceptions import RetrievalError
from app.models import (
    Citation,
    CitationLocator,
    CsvCitationLocator,
    DatabaseCitationLocator,
    Evidence,
    GraphPathSupport,
    GraphSupport,
    PdfCitationLocator,
    SourceType,
    WebsiteCitationLocator,
)

_WHITESPACE = re.compile(r"\s+")
_MAX_EXCERPT_LENGTH = 1200


def build_citations(evidence: list[Evidence]) -> list[Citation]:
    """Assign deterministic IDs and preserve typed source-specific locations."""
    citations: list[Citation] = []
    citation_by_identity: dict[tuple[Hashable, ...], Citation] = {}
    for item in evidence:
        if item.raw_score is None:
            raise RetrievalError("Retrieved evidence is missing a similarity score")
        item_citations: list[str] = []
        supports = _graph_supports(item)
        edge_citations: dict[str, list[str]] = {}
        if supports:
            for path_support in supports:
                support = path_support.support
                locator_details = _support_locator_details(support)
                excerpt = _bounded_excerpt(support.text)
                citation = _get_or_create_citation(
                    citations,
                    citation_by_identity,
                    evidence=item,
                    source_id=support.source_id,
                    source_type=support.source_type,
                    source_title=support.title,
                    excerpt=excerpt,
                    locator_details=locator_details,
                    locator=_display_locator(locator_details),
                )
                item_citations.append(citation.citation_id)
                relationship_id = str(path_support.relationship_id)
                edge_citations.setdefault(relationship_id, []).append(citation.citation_id)
            item.metadata["edge_citation_ids"] = {
                relationship_id: list(dict.fromkeys(citation_ids))
                for relationship_id, citation_ids in edge_citations.items()
            }
        else:
            title = item.metadata.get("title")
            locator_details = _locator_details(item)
            citation = _get_or_create_citation(
                citations,
                citation_by_identity,
                evidence=item,
                source_id=item.source_id,
                source_type=item.source_type,
                source_title=title if isinstance(title, str) else None,
                excerpt=_bounded_excerpt(item.content),
                locator_details=locator_details,
                locator=_display_locator(locator_details),
            )
            item_citations.append(citation.citation_id)
        item.metadata["citation_ids"] = list(dict.fromkeys(item_citations))
    return citations


def _get_or_create_citation(
    citations: list[Citation],
    citation_by_identity: dict[tuple[Hashable, ...], Citation],
    *,
    evidence: Evidence,
    source_id: Hashable,
    source_type: SourceType,
    source_title: str | None,
    excerpt: str,
    locator_details: CitationLocator,
    locator: str,
) -> Citation:
    locator_identity = tuple(
        sorted(locator_details.model_dump(mode="json", exclude_none=True).items())
    )
    identity = (source_id, locator_identity, _WHITESPACE.sub(" ", excerpt).casefold())
    existing = citation_by_identity.get(identity)
    if existing is not None:
        return existing
    citation = Citation(
        citation_id=f"S{len(citations) + 1}",
        evidence_id=evidence.evidence_id,
        source_id=source_id,
        source_type=source_type,
        source_title=source_title,
        excerpt=excerpt,
        locator=locator,
        locator_details=locator_details,
        score=evidence.raw_score,
        retriever=evidence.retriever,
    )
    citations.append(citation)
    citation_by_identity[identity] = citation
    return citation


def _bounded_excerpt(text: str) -> str:
    normalized = _WHITESPACE.sub(" ", text).strip()
    if not normalized:
        raise RetrievalError("Retrieved evidence has no citation excerpt")
    if len(normalized) <= _MAX_EXCERPT_LENGTH:
        return normalized
    return f"{normalized[: _MAX_EXCERPT_LENGTH - 1].rstrip()}…"


def _locator_details(evidence: Evidence) -> CitationLocator:
    if evidence.source_type is SourceType.PDF and evidence.page_number is not None:
        return PdfCitationLocator(page_number=evidence.page_number)
    if evidence.source_type is SourceType.WEBSITE and evidence.source_uri:
        return WebsiteCitationLocator(url=evidence.source_uri)
    if evidence.source_type is SourceType.CSV and evidence.row_id:
        return CsvCitationLocator(row_id=evidence.row_id)
    if evidence.source_type is SourceType.DATABASE and evidence.table_name:
        fingerprint = evidence.metadata.get("query_fingerprint")
        return DatabaseCitationLocator(
            table_name=evidence.table_name,
            row_id=evidence.row_id,
            query_fingerprint=str(fingerprint) if fingerprint else None,
        )
    raise RetrievalError("Retrieved evidence has no source-specific citation locator")


def _graph_supports(evidence: Evidence) -> list[GraphPathSupport]:
    if evidence.retriever != "graph" and "citation_supports" not in evidence.metadata:
        return []
    payload = evidence.metadata.get("citation_supports")
    if not isinstance(payload, list):
        raise RetrievalError("Graph evidence is missing citation support records")
    try:
        return [GraphPathSupport.model_validate(item) for item in payload]
    except ValueError as error:
        raise RetrievalError("Graph evidence contains invalid citation support records") from error


def _support_locator_details(support: GraphSupport) -> CitationLocator:
    if support.source_type is SourceType.PDF and support.page_number is not None:
        return PdfCitationLocator(page_number=support.page_number)
    if support.source_type is SourceType.WEBSITE and support.source_uri:
        return WebsiteCitationLocator(url=support.source_uri)
    if support.source_type is SourceType.CSV and support.row_id:
        return CsvCitationLocator(row_id=support.row_id)
    if support.source_type is SourceType.DATABASE and support.table_name:
        return DatabaseCitationLocator(table_name=support.table_name, row_id=support.row_id)
    raise RetrievalError("Graph support has no source-specific citation locator")


def _display_locator(locator: CitationLocator) -> str:
    if isinstance(locator, PdfCitationLocator):
        return f"page {locator.page_number}"
    if isinstance(locator, WebsiteCitationLocator):
        return str(locator.url)
    if isinstance(locator, CsvCitationLocator):
        return f"row {locator.row_id}"
    if locator.row_id:
        return f"table {locator.table_name}, record {locator.row_id}"
    suffix = f", query {locator.query_fingerprint[:12]}" if locator.query_fingerprint else ""
    return f"table {locator.table_name}{suffix}"

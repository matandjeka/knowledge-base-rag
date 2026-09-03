"""Request-local citation construction for canonical retrieval evidence."""

from app.core.exceptions import RetrievalError
from app.models import Citation, Evidence, GraphPathSupport, GraphSupport, SourceType


def build_citations(evidence: list[Evidence]) -> list[Citation]:
    """Assign request-local IDs and preserve source-specific locators."""
    citations: list[Citation] = []
    next_index = 1
    for item in evidence:
        if item.raw_score is None:
            raise RetrievalError("Retrieved evidence is missing a similarity score")
        item_citations: list[str] = []
        supports = _graph_supports(item)
        if supports:
            citation_by_locator: dict[tuple[object, str], str] = {}
            edge_citations: dict[str, list[str]] = {}
            for path_support in supports:
                support = path_support.support
                locator = _support_locator(support)
                identity = (support.source_id, locator)
                citation_id = citation_by_locator.get(identity)
                if citation_id is None:
                    citation_id = f"S{next_index}"
                    next_index += 1
                    citation_by_locator[identity] = citation_id
                    citations.append(
                        Citation(
                            citation_id=citation_id,
                            evidence_id=item.evidence_id,
                            source_id=support.source_id,
                            source_type=support.source_type,
                            source_title=support.title,
                            excerpt=support.text,
                            locator=locator,
                            score=item.raw_score,
                        )
                    )
                    item_citations.append(citation_id)
                relationship_id = str(path_support.relationship_id)
                edge_citations.setdefault(relationship_id, []).append(citation_id)
            item.metadata["edge_citation_ids"] = {
                relationship_id: list(dict.fromkeys(citation_ids))
                for relationship_id, citation_ids in edge_citations.items()
            }
        else:
            title = item.metadata.get("title")
            citation_id = f"S{next_index}"
            next_index += 1
            citations.append(
                Citation(
                    citation_id=citation_id,
                    evidence_id=item.evidence_id,
                    source_id=item.source_id,
                    source_type=item.source_type,
                    source_title=title if isinstance(title, str) else None,
                    excerpt=item.content,
                    locator=_locator(item),
                    score=item.raw_score,
                )
            )
            item_citations.append(citation_id)
        item.metadata["citation_ids"] = item_citations
    return citations


def _locator(evidence: Evidence) -> str:
    if evidence.source_type is SourceType.PDF and evidence.page_number is not None:
        return f"page {evidence.page_number}"
    if evidence.source_type is SourceType.WEBSITE and evidence.source_uri:
        return evidence.source_uri
    if evidence.source_type is SourceType.CSV and evidence.row_id:
        return f"row {evidence.row_id}"
    raise RetrievalError("Retrieved evidence has no source-specific citation locator")


def _graph_supports(evidence: Evidence) -> list[GraphPathSupport]:
    if evidence.retriever != "graph":
        return []
    payload = evidence.metadata.get("citation_supports")
    if not isinstance(payload, list):
        raise RetrievalError("Graph evidence is missing citation support records")
    try:
        return [GraphPathSupport.model_validate(item) for item in payload]
    except ValueError as error:
        raise RetrievalError("Graph evidence contains invalid citation support records") from error


def _support_locator(support: GraphSupport) -> str:
    if support.source_type is SourceType.PDF and support.page_number is not None:
        return f"page {support.page_number}"
    if support.source_type is SourceType.WEBSITE and support.source_uri:
        return support.source_uri
    if support.source_type is SourceType.CSV and support.row_id:
        return f"row {support.row_id}"
    raise RetrievalError("Graph support has no source-specific citation locator")

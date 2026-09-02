"""Request-local citation construction for baseline retrieval evidence."""

from app.core.exceptions import RetrievalError
from app.models import Citation, Evidence, SourceType


def build_citations(evidence: list[Evidence]) -> list[Citation]:
    """Assign request-local IDs and preserve source-specific locators."""
    citations: list[Citation] = []
    for index, item in enumerate(evidence, start=1):
        if item.raw_score is None:
            raise RetrievalError("Retrieved evidence is missing a similarity score")
        title = item.metadata.get("title")
        citations.append(
            Citation(
                citation_id=f"S{index}",
                source_id=item.source_id,
                source_type=item.source_type,
                source_title=title if isinstance(title, str) else None,
                excerpt=item.content,
                locator=_locator(item),
                score=item.raw_score,
            )
        )
    return citations


def _locator(evidence: Evidence) -> str:
    if evidence.source_type is SourceType.PDF and evidence.page_number is not None:
        return f"page {evidence.page_number}"
    if evidence.source_type is SourceType.WEBSITE and evidence.source_uri:
        return evidence.source_uri
    if evidence.source_type is SourceType.CSV and evidence.row_id:
        return f"row {evidence.row_id}"
    raise RetrievalError("Retrieved evidence has no source-specific citation locator")

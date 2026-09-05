"""Provider-neutral validation for inline answer citation markers."""

import re

from app.core.exceptions import GenerationError
from app.models import AnswerCitationSegment, Citation

_VALID_MARKER = re.compile(r"\[S[1-9][0-9]*\]")
_MARKER_LIKE = re.compile(r"\[S[^\]\n]*\]")
_TRAILING_MARKERS = re.compile(r"(?:\s*\[S[1-9][0-9]*\])+\s*$")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_WHITESPACE = re.compile(r"\s+")


def validate_inline_citations(
    answer: str,
    citations: list[Citation],
    *,
    insufficient_evidence: bool,
) -> list[AnswerCitationSegment]:
    """Resolve every cited answer paragraph to known response citations."""
    if insufficient_evidence:
        return []
    known = {citation.citation_id for citation in citations}
    if not known:
        raise GenerationError("A grounded answer requires at least one citation")
    segments: list[AnswerCitationSegment] = []
    for paragraph in _PARAGRAPH_BREAK.split(answer.strip()):
        if not paragraph.strip():
            continue
        marker_like = _MARKER_LIKE.findall(paragraph)
        markers = _VALID_MARKER.findall(paragraph)
        if marker_like != markers:
            raise GenerationError("Generated answer contains a malformed citation marker")
        trailing_match = _TRAILING_MARKERS.search(paragraph)
        trailing_markers = _VALID_MARKER.findall(trailing_match.group()) if trailing_match else []
        if markers and markers != trailing_markers:
            raise GenerationError("Generated answer contains a misplaced citation marker")
        citation_ids = list(dict.fromkeys(marker[1:-1] for marker in markers))
        unknown = [citation_id for citation_id in citation_ids if citation_id not in known]
        if unknown:
            raise GenerationError("Generated answer references an unknown citation")
        if not citation_ids:
            raise GenerationError("Generated answer contains an uncited factual segment")
        assert trailing_match is not None
        text = _WHITESPACE.sub(" ", paragraph[: trailing_match.start()]).strip()
        if not text:
            raise GenerationError("Generated answer contains an empty cited segment")
        segments.append(AnswerCitationSegment(text=text, citation_ids=citation_ids))
    if not segments:
        raise GenerationError("Generated answer contains no factual segments")
    return segments

"""Provider-neutral generation contract and deterministic extractive baseline."""

from typing import Protocol

from app.models import Citation, Evidence

INSUFFICIENT_EVIDENCE_ANSWER = (
    "I could not find enough evidence in the indexed sources to answer reliably."
)


class Generator(Protocol):
    """Create an evidence-grounded answer from ordered retrieval results."""

    async def generate(
        self, question: str, evidence: list[Evidence], citations: list[Citation]
    ) -> str:
        """Return an answer whose citation markers reference the supplied citations."""
        ...


class ExtractiveGenerator:
    """Return top evidence passages verbatim with request-local citation markers."""

    def __init__(self, max_passages: int = 3) -> None:
        self._max_passages = max_passages

    async def generate(
        self, question: str, evidence: list[Evidence], citations: list[Citation]
    ) -> str:
        del question
        if not evidence:
            return INSUFFICIENT_EVIDENCE_ANSWER
        passages = [
            f"{item.content} [{citation.citation_id}]"
            for item, citation in zip(
                evidence[: self._max_passages],
                citations[: self._max_passages],
                strict=True,
            )
        ]
        return "\n\n".join(passages)

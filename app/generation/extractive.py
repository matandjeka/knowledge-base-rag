"""Provider-neutral generation contract and deterministic extractive baseline."""

from dataclasses import dataclass
from typing import Protocol

from app.models import Citation, Evidence

INSUFFICIENT_EVIDENCE_ANSWER = (
    "I could not find enough evidence in the indexed sources to answer reliably."
)


@dataclass(frozen=True, slots=True)
class GeneratedAnswer:
    """Provider-neutral answer text and its explicit evidence disposition."""

    answer: str
    insufficient_evidence: bool


class Generator(Protocol):
    """Create an evidence-grounded answer from ordered retrieval results."""

    async def generate(
        self, question: str, evidence: list[Evidence], citations: list[Citation]
    ) -> GeneratedAnswer:
        """Return cited answer text and an explicit evidence disposition."""
        ...


class ExtractiveGenerator:
    """Return top evidence passages verbatim with request-local citation markers."""

    def __init__(self, max_passages: int = 3) -> None:
        self._max_passages = max_passages

    async def generate(
        self, question: str, evidence: list[Evidence], citations: list[Citation]
    ) -> GeneratedAnswer:
        del question
        if not evidence:
            return GeneratedAnswer(
                answer=INSUFFICIENT_EVIDENCE_ANSWER,
                insufficient_evidence=True,
            )
        passages = []
        for item in evidence[: self._max_passages]:
            citation_ids = item.metadata.get("citation_ids", [])
            markers = " ".join(f"[{citation_id}]" for citation_id in citation_ids)
            passages.append(f"{item.content} {markers}".rstrip())
        return GeneratedAnswer(answer="\n\n".join(passages), insufficient_evidence=False)

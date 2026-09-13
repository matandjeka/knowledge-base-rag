"""Provider-neutral generation contract and deterministic extractive baseline."""

import re
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


def _literal_source_label(match: re.Match[str]) -> str:
    """Use display brackets to distinguish source text from citation syntax."""
    return match.group().replace("[", "\uff3b").replace("]", "\uff3d")


class ExtractiveGenerator:
    """Return evidence paragraphs with source labels distinct from citation markers."""

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
            # Source-authored labels must never become request-local citations, even
            # when they happen to match an assigned ID. Original evidence stays intact.
            content = re.sub(
                r"\[S[^\]\n]*\]?",
                _literal_source_label,
                item.content,
            )
            for paragraph in re.split(r"\n\s*\n", content):
                if paragraph.strip():
                    passages.append(f"{paragraph.strip()} {markers}".rstrip())
        return GeneratedAnswer(answer="\n\n".join(passages), insufficient_evidence=False)

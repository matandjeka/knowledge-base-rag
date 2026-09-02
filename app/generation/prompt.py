"""Deterministic grounded-prompt construction for future generation providers."""

from app.models import Citation, Evidence


def build_grounded_prompt(
    question: str, evidence: list[Evidence], citations: list[Citation]
) -> str:
    """Build a provider-neutral prompt that forbids unsupported claims."""
    context = "\n\n".join(
        f"[{citation.citation_id}] {item.content}"
        for item, citation in zip(evidence, citations, strict=True)
    )
    return (
        "Answer only from the supplied evidence. Cite every factual claim using the "
        "provided source IDs. If the evidence is insufficient, say so explicitly.\n\n"
        f"Question:\n{question}\n\nEvidence:\n{context or '(none)'}"
    )

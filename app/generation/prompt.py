"""Deterministic grounded-prompt construction for future generation providers."""

from app.models import Citation, Evidence


def build_grounded_prompt(
    question: str, evidence: list[Evidence], citations: list[Citation]
) -> str:
    """Build a provider-neutral prompt that forbids unsupported claims."""
    context_parts = []
    for item in evidence:
        markers = " ".join(
            f"[{citation.citation_id}]"
            for citation in citations
            if citation.evidence_id == item.evidence_id
        )
        context_parts.append(f"{markers} {item.content}".strip())
    context = "\n\n".join(context_parts)
    return (
        "Answer only from the supplied evidence. Cite every factual claim using the "
        "provided source IDs. If the evidence is insufficient, say so explicitly.\n\n"
        f"Question:\n{question}\n\nEvidence:\n{context or '(none)'}"
    )

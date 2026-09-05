"""Deterministic grounded-prompt construction for future generation providers."""

from app.models import Citation, Evidence


def build_grounded_prompt(
    question: str, evidence: list[Evidence], citations: list[Citation]
) -> str:
    """Build a provider-neutral prompt that forbids unsupported claims."""
    context_parts = []
    for item in evidence:
        citation_ids = item.metadata.get("citation_ids", [])
        markers = " ".join(f"[{citation_id}]" for citation_id in citation_ids)
        context_parts.append(f"{markers} {item.content}".strip())
    context = "\n\n".join(context_parts)
    return (
        "Answer only from the supplied evidence. End every non-empty factual paragraph with one "
        "or more exact citation markers such as [S1]. Use only the provided source IDs. Never "
        "invent or alter a marker. If the evidence is insufficient, say so explicitly.\n\n"
        f"Question:\n{question}\n\nEvidence:\n{context or '(none)'}"
    )

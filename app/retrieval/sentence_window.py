"""Deterministic sentence parsing and sentence-window retrieval."""

import re
from dataclasses import dataclass
from uuid import UUID, uuid4

from app.core.exceptions import IndexingError
from app.models import Evidence, NormalizedDocument
from app.retrieval.embedding import EmbeddingService
from app.retrieval.vector_store import VectorIndexKind, VectorStore

_BOUNDARY = re.compile(r"(?<=[.!?])(?:[\"')\]]*)\s+|\n+")
_ABBREVIATIONS = frozenset(
    {
        "dr.",
        "e.g.",
        "etc.",
        "i.e.",
        "jr.",
        "mr.",
        "mrs.",
        "ms.",
        "prof.",
        "sr.",
        "st.",
        "u.s.",
        "vs.",
    }
)


@dataclass(frozen=True, slots=True)
class ParsedSentence:
    """One meaningful sentence and its offsets in the parent text."""

    text: str
    index: int
    start: int
    end: int


def parse_sentences(text: str) -> tuple[ParsedSentence, ...]:
    """Split text deterministically while retaining stable character offsets."""
    candidates: list[tuple[int, int]] = []
    start = 0
    for boundary in _BOUNDARY.finditer(text):
        closing = re.match(r"[\"')\]]*", boundary.group())
        end = boundary.start() + (len(closing.group()) if closing is not None else 0)
        prefix = text[start:end].rstrip()
        token = prefix.rsplit(maxsplit=1)[-1].lower() if prefix else ""
        if token in _ABBREVIATIONS or _looks_like_initial(token) or _looks_like_decimal(prefix):
            continue
        candidates.append((start, end))
        start = boundary.end()
    candidates.append((start, len(text)))

    sentences: list[ParsedSentence] = []
    for raw_start, raw_end in candidates:
        content = text[raw_start:raw_end]
        left_trimmed = len(content) - len(content.lstrip())
        stripped = content.strip()
        if not stripped or not any(character.isalnum() for character in stripped):
            continue
        sentence_start = raw_start + left_trimmed
        sentences.append(
            ParsedSentence(
                text=stripped,
                index=len(sentences),
                start=sentence_start,
                end=sentence_start + len(stripped),
            )
        )
    return tuple(sentences)


def build_sentence_window_documents(
    documents: list[NormalizedDocument], *, radius: int
) -> list[NormalizedDocument]:
    """Create sentence retrieval nodes with bounded neighboring context metadata."""
    if radius < 0:
        raise ValueError("Sentence-window radius cannot be negative")
    nodes: list[NormalizedDocument] = []
    for document in documents:
        sentences = parse_sentences(document.content)
        for sentence in sentences:
            window_start = max(0, sentence.index - radius)
            window_end = min(len(sentences), sentence.index + radius + 1)
            window = " ".join(item.text for item in sentences[window_start:window_end])
            nodes.append(
                document.model_copy(
                    update={
                        "document_id": uuid4(),
                        "content": sentence.text,
                        "metadata": {
                            **document.metadata,
                            "sentence_window": {
                                "parent_document_id": str(document.document_id),
                                "matched_sentence": sentence.text,
                                "sentence_index": sentence.index,
                                "character_start": sentence.start,
                                "character_end": sentence.end,
                                "window_start": window_start,
                                "window_end": window_end,
                                "window_radius": radius,
                                "window_text": window,
                            },
                        },
                    },
                    deep=True,
                )
            )
    return nodes


class SentenceWindowRetriever:
    """Retrieve sentences, expand them to stored windows, and remove exact duplicates."""

    def __init__(self, embeddings: EmbeddingService, vector_store: VectorStore) -> None:
        self._embeddings = embeddings
        self._vector_store = vector_store

    async def retrieve(
        self,
        workspace_id: str,
        query: str,
        *,
        top_k: int,
        source_ids: frozenset[UUID],
        min_similarity: float,
    ) -> list[Evidence]:
        query_vector = await self._embeddings.embed_query(query)
        matches = await self._vector_store.search(
            workspace_id,
            query_vector,
            top_k=top_k * 5,
            source_ids=source_ids,
            model_name=self._embeddings.model_name,
            dimension=self._embeddings.dimension,
            index_kind=VectorIndexKind.SENTENCE_WINDOW,
        )
        evidence: list[Evidence] = []
        seen: set[tuple[str, int, int]] = set()
        for match in matches:
            raw_score = min(1.0, max(-1.0, match.score))
            if raw_score < min_similarity:
                continue
            document = match.document
            window = document.metadata.get("sentence_window")
            if not isinstance(window, dict):
                raise IndexingError("Sentence-window match is missing expansion metadata")
            window_text = window.get("window_text")
            parent_id = window.get("parent_document_id")
            window_start = window.get("window_start")
            window_end = window.get("window_end")
            if (
                not isinstance(window_text, str)
                or not window_text
                or not isinstance(parent_id, str)
                or not isinstance(window_start, int)
                or not isinstance(window_end, int)
            ):
                raise IndexingError("Sentence-window match contains invalid expansion metadata")
            identity = (parent_id, window_start, window_end)
            if identity in seen:
                continue
            seen.add(identity)
            evidence.append(
                Evidence(
                    retriever="sentence_window",
                    content=window_text,
                    source_id=document.source_id,
                    source_type=document.source_type,
                    raw_score=raw_score,
                    normalized_score=(raw_score + 1.0) / 2.0,
                    source_uri=document.source_uri,
                    page_number=document.page_number,
                    row_id=document.row_id,
                    table_name=document.table_name,
                    metadata={
                        "document_id": str(document.document_id),
                        "title": document.title,
                        "document_metadata": document.metadata,
                        **window,
                    },
                )
            )
            if len(evidence) == top_k:
                break
        return evidence


def _looks_like_initial(token: str) -> bool:
    return len(token) == 2 and token[0].isalpha() and token[1] == "."


def _looks_like_decimal(prefix: str) -> bool:
    return bool(re.search(r"\d+\.\d+$", prefix))

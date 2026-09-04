"""Durable workspace-scoped BM25 indexing and retrieval."""

import asyncio
import hashlib
import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.core.exceptions import IndexingError, IndexNotFoundError
from app.models import Evidence, NormalizedDocument

_DOCUMENTS_ADAPTER = TypeAdapter(list[NormalizedDocument])
_TOKEN_PATTERN = re.compile(r"[^\W_]+(?:[-_][^\W_]+)*", re.UNICODE)
_FORMAT_VERSION = 1


def tokenize_lexical(text: str) -> tuple[str, ...]:
    """Return deterministic Unicode tokens while preserving enterprise identifiers."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return tuple(match.group(0) for match in _TOKEN_PATTERN.finditer(normalized))


class LexicalIndexMetadata(BaseModel):
    """Compatibility and integrity metadata for one immutable lexical generation."""

    model_config = ConfigDict(extra="forbid")

    generation_id: UUID
    format_version: int = Field(ge=1)
    document_count: int = Field(ge=1)
    index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    documents_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class LexicalIndexData(BaseModel):
    """Serializable statistics required to score one BM25 corpus."""

    model_config = ConfigDict(extra="forbid")

    average_document_length: float = Field(gt=0)
    document_lengths: list[int]
    term_frequencies: list[dict[str, int]]
    title_term_frequencies: list[dict[str, int]]
    document_frequencies: dict[str, int]


class LexicalSearchMatch(BaseModel):
    """One lexical match with both raw and query-relative scores."""

    model_config = ConfigDict(extra="forbid")

    document: NormalizedDocument
    score: float = Field(ge=0)
    normalized_score: float = Field(ge=0, le=1)


class LocalLexicalStore:
    """Persist immutable BM25 generations beneath a controlled local root."""

    def __init__(self, root: Path, *, k1: float = 1.5, b: float = 0.75) -> None:
        if k1 <= 0:
            raise ValueError("BM25 k1 must be positive")
        if not 0 <= b <= 1:
            raise ValueError("BM25 b must be between 0 and 1")
        self._root = (root / "lexical-indexes" / "workspaces").resolve()
        self._k1 = k1
        self._b = b
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def prepare(
        self,
        workspace_id: str,
        documents: Sequence[NormalizedDocument],
        *,
        generation_id: UUID | None = None,
    ) -> LexicalIndexMetadata:
        """Write an immutable lexical generation without activating it."""
        if not documents:
            raise IndexingError("Cannot build the lexical index without documents")
        async with self._locks[workspace_id]:
            return await asyncio.to_thread(
                self._write_generation,
                workspace_id,
                list(documents),
                generation_id or uuid4(),
            )

    async def activate(self, workspace_id: str, generation_id: UUID) -> None:
        """Atomically point one workspace at a prepared lexical generation."""
        async with self._locks[workspace_id]:
            await asyncio.to_thread(self._activate_generation, workspace_id, generation_id)

    async def search(
        self,
        workspace_id: str,
        query: str,
        *,
        top_k: int,
        source_ids: frozenset[UUID],
        min_score: float,
        title_boost: float,
    ) -> tuple[LexicalSearchMatch, ...]:
        """Score the current workspace generation with BM25."""
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if min_score < 0:
            raise ValueError("Lexical minimum score cannot be negative")
        if title_boost < 0:
            raise ValueError("Lexical title boost cannot be negative")
        async with self._locks[workspace_id]:
            return await asyncio.to_thread(
                self._search_sync,
                workspace_id,
                query,
                top_k,
                source_ids,
                min_score,
                title_boost,
            )

    def _write_generation(
        self,
        workspace_id: str,
        documents: list[NormalizedDocument],
        generation_id: UUID,
    ) -> LexicalIndexMetadata:
        directory = self._workspace_directory(workspace_id) / "generations" / str(generation_id)
        directory.mkdir(parents=True, exist_ok=False)
        term_frequencies: list[dict[str, int]] = []
        title_term_frequencies: list[dict[str, int]] = []
        document_lengths: list[int] = []
        document_frequencies: Counter[str] = Counter()
        for document in documents:
            body_counts = Counter(tokenize_lexical(document.content))
            title_counts = Counter(tokenize_lexical(document.title or ""))
            term_frequencies.append(dict(body_counts))
            title_term_frequencies.append(dict(title_counts))
            document_lengths.append(max(1, sum(body_counts.values())))
            document_frequencies.update(body_counts.keys() | title_counts.keys())
        index = LexicalIndexData(
            average_document_length=sum(document_lengths) / len(document_lengths),
            document_lengths=document_lengths,
            term_frequencies=term_frequencies,
            title_term_frequencies=title_term_frequencies,
            document_frequencies=dict(document_frequencies),
        )
        index_payload = index.model_dump_json().encode()
        documents_payload = _DOCUMENTS_ADAPTER.dump_json(documents)
        (directory / "index.json").write_bytes(index_payload)
        (directory / "documents.json").write_bytes(documents_payload)
        metadata = LexicalIndexMetadata(
            generation_id=generation_id,
            format_version=_FORMAT_VERSION,
            document_count=len(documents),
            index_sha256=_sha256(index_payload),
            documents_sha256=_sha256(documents_payload),
        )
        (directory / "metadata.json").write_text(metadata.model_dump_json(indent=2))
        return metadata

    def _activate_generation(self, workspace_id: str, generation_id: UUID) -> None:
        workspace_directory = self._workspace_directory(workspace_id)
        if not (workspace_directory / "generations" / str(generation_id)).is_dir():
            raise IndexingError("Prepared lexical-index generation does not exist")
        workspace_directory.mkdir(parents=True, exist_ok=True)
        temporary = workspace_directory / "CURRENT.tmp"
        temporary.write_text(str(generation_id))
        temporary.replace(workspace_directory / "CURRENT")

    def _search_sync(
        self,
        workspace_id: str,
        query: str,
        top_k: int,
        source_ids: frozenset[UUID],
        min_score: float,
        title_boost: float,
    ) -> tuple[LexicalSearchMatch, ...]:
        workspace_directory = self._workspace_directory(workspace_id)
        current = workspace_directory / "CURRENT"
        if not current.is_file():
            raise IndexNotFoundError("No lexical index exists for this workspace")
        try:
            generation_id = UUID(current.read_text().strip())
            directory = workspace_directory / "generations" / str(generation_id)
            metadata = LexicalIndexMetadata.model_validate_json(
                (directory / "metadata.json").read_text()
            )
            index_payload = (directory / "index.json").read_bytes()
            documents_payload = (directory / "documents.json").read_bytes()
        except (OSError, ValueError) as error:
            raise IndexingError(
                "The current lexical-index generation is unreadable; rebuild the source index"
            ) from error
        if metadata.generation_id != generation_id or metadata.format_version != _FORMAT_VERSION:
            raise IndexingError("Lexical-index generation metadata is incompatible")
        if _sha256(index_payload) != metadata.index_sha256:
            raise IndexingError("Lexical-index checksum validation failed")
        if _sha256(documents_payload) != metadata.documents_sha256:
            raise IndexingError("Lexical document-mapping checksum validation failed")
        try:
            index = LexicalIndexData.model_validate_json(index_payload)
            documents = _DOCUMENTS_ADAPTER.validate_json(documents_payload)
        except ValueError as error:
            raise IndexingError("The current lexical-index generation is invalid") from error
        count = len(documents)
        if not (
            count
            == metadata.document_count
            == len(index.document_lengths)
            == len(index.term_frequencies)
            == len(index.title_term_frequencies)
        ):
            raise IndexingError("Lexical index and document mapping are out of sync")
        query_terms = tokenize_lexical(query)
        if not query_terms:
            return ()
        scores: list[tuple[float, str, NormalizedDocument]] = []
        for position, document in enumerate(documents):
            if source_ids and document.source_id not in source_ids:
                continue
            score = self._score_document(index, position, query_terms, title_boost)
            if score > 0 and score >= min_score:
                scores.append((score, str(document.document_id), document))
        scores.sort(key=lambda item: (-item[0], item[1]))
        selected = scores[:top_k]
        maximum = selected[0][0] if selected else 0.0
        return tuple(
            LexicalSearchMatch(
                document=document,
                score=score,
                normalized_score=score / maximum,
            )
            for score, _, document in selected
        )

    def _score_document(
        self,
        index: LexicalIndexData,
        position: int,
        query_terms: tuple[str, ...],
        title_boost: float,
    ) -> float:
        score = 0.0
        corpus_size = len(index.document_lengths)
        length = index.document_lengths[position]
        frequencies = index.term_frequencies[position]
        title_frequencies = index.title_term_frequencies[position]
        for term in query_terms:
            frequency = frequencies.get(term, 0)
            title_frequency = title_frequencies.get(term, 0)
            if not frequency and not title_frequency:
                continue
            document_frequency = index.document_frequencies[term]
            inverse_document_frequency = math.log(
                1 + (corpus_size - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            weighted_frequency = frequency + title_boost * title_frequency
            denominator = weighted_frequency + self._k1 * (
                1 - self._b + self._b * length / index.average_document_length
            )
            score += inverse_document_frequency * (
                weighted_frequency * (self._k1 + 1) / denominator
            )
        return score

    def _workspace_directory(self, workspace_id: str) -> Path:
        if not workspace_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in workspace_id
        ):
            raise ValueError("Invalid workspace identifier")
        directory = (self._root / workspace_id).resolve()
        if not directory.is_relative_to(self._root):
            raise ValueError("Lexical index path escapes the configured data directory")
        return directory


class LexicalRetriever:
    """Map workspace BM25 matches into canonical evidence."""

    def __init__(self, store: LocalLexicalStore, *, title_boost: float = 0.5) -> None:
        self._store = store
        self._title_boost = title_boost

    async def retrieve(
        self,
        workspace_id: str,
        query: str,
        *,
        top_k: int,
        source_ids: frozenset[UUID],
        min_similarity: float,
    ) -> list[Evidence]:
        """Return canonical lexical evidence ordered by BM25 score."""
        matches = await self._store.search(
            workspace_id,
            query,
            top_k=top_k,
            source_ids=source_ids,
            min_score=min_similarity,
            title_boost=self._title_boost,
        )
        return [
            Evidence(
                retriever="lexical",
                content=match.document.content,
                source_id=match.document.source_id,
                source_type=match.document.source_type,
                raw_score=match.score,
                normalized_score=match.normalized_score,
                source_uri=match.document.source_uri,
                page_number=match.document.page_number,
                row_id=match.document.row_id,
                table_name=match.document.table_name,
                metadata={
                    "document_id": str(match.document.document_id),
                    "title": match.document.title,
                    "document_metadata": match.document.metadata,
                },
            )
            for match in matches
        ]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()

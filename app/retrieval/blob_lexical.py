"""Azure Blob backed immutable BM25 generations with bounded read-through caching."""

import hashlib
import math
from collections import Counter, OrderedDict
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from app.core.exceptions import IndexingError
from app.models import NormalizedDocument
from app.persistence import ActiveGenerationRepository, GenerationKind
from app.retrieval.lexical import (
    LexicalIndexData,
    LexicalIndexMetadata,
    LexicalSearchMatch,
    tokenize_lexical,
)

_DOCUMENTS = TypeAdapter(list[NormalizedDocument])
_FORMAT_VERSION = 1


class BlobLexicalStore:
    """Persist BM25 data in Blob and resolve visibility through PostgreSQL metadata."""

    def __init__(
        self,
        container_client: Any,
        generation_repository: ActiveGenerationRepository,
        *,
        k1: float = 1.5,
        b: float = 0.75,
        max_cached_generations: int = 8,
    ) -> None:
        self._container = container_client
        self._generations = generation_repository
        self._k1 = k1
        self._b = b
        self._cache: OrderedDict[
            tuple[str, UUID],
            tuple[LexicalIndexMetadata, LexicalIndexData, list[NormalizedDocument]],
        ] = OrderedDict()
        self._max_cached_generations = max_cached_generations

    async def prepare(
        self,
        workspace_id: str,
        documents: Sequence[NormalizedDocument],
        *,
        generation_id: UUID | None = None,
    ) -> LexicalIndexMetadata:
        if not documents:
            raise IndexingError("Cannot build the lexical index without documents")
        generation_id = generation_id or uuid4()
        index = _build_index(documents)
        index_payload = index.model_dump_json().encode()
        documents_payload = _DOCUMENTS.dump_json(list(documents))
        metadata = LexicalIndexMetadata(
            generation_id=generation_id,
            format_version=_FORMAT_VERSION,
            document_count=len(documents),
            index_sha256=_sha256(index_payload),
            documents_sha256=_sha256(documents_payload),
        )
        prefix = self._prefix(workspace_id, generation_id)
        await self._upload(f"{prefix}/index.json", index_payload)
        await self._upload(f"{prefix}/documents.json", documents_payload)
        await self._upload(f"{prefix}/metadata.json", metadata.model_dump_json().encode())
        return metadata

    async def activate(self, workspace_id: str, generation_id: UUID) -> None:
        metadata, _, _ = await self._load(workspace_id, generation_id)
        if metadata.generation_id != generation_id:
            raise IndexingError("Prepared lexical generation has inconsistent metadata")

    async def active_generation(self, workspace_id: str) -> UUID:
        return await self._generations.active_generation(workspace_id, GenerationKind.RETRIEVAL)

    async def restore_activation(self, workspace_id: str, generation_id: UUID | None) -> None:
        """Compatibility hook; authoritative activation is changed only by the coordinator."""
        if generation_id is not None:
            await self._load(workspace_id, generation_id)

    async def search(
        self,
        workspace_id: str,
        query: str,
        *,
        top_k: int,
        source_ids: frozenset[UUID],
        min_score: float,
        title_boost: float,
        expected_generation_id: UUID | None = None,
    ) -> tuple[LexicalSearchMatch, ...]:
        generation_id = await self.active_generation(workspace_id)
        if expected_generation_id is not None and expected_generation_id != generation_id:
            raise IndexingError("Lexical and vector active generations do not match")
        _, index, documents = await self._load(workspace_id, generation_id)
        query_terms = tokenize_lexical(query)
        scores: list[tuple[float, str, NormalizedDocument]] = []
        for position, document in enumerate(documents):
            if source_ids and document.source_id not in source_ids:
                continue
            score = _score(index, position, query_terms, title_boost, self._k1, self._b)
            if score > 0 and score >= min_score:
                scores.append((score, str(document.document_id), document))
        scores.sort(key=lambda item: (-item[0], item[1]))
        selected = scores[:top_k]
        maximum = selected[0][0] if selected else 0.0
        return tuple(
            LexicalSearchMatch(document=document, score=score, normalized_score=score / maximum)
            for score, _, document in selected
        )

    async def _load(
        self, workspace_id: str, generation_id: UUID
    ) -> tuple[LexicalIndexMetadata, LexicalIndexData, list[NormalizedDocument]]:
        key = (workspace_id, generation_id)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        prefix = self._prefix(workspace_id, generation_id)
        metadata_payload = await self._download(f"{prefix}/metadata.json")
        index_payload = await self._download(f"{prefix}/index.json")
        documents_payload = await self._download(f"{prefix}/documents.json")
        try:
            metadata = LexicalIndexMetadata.model_validate_json(metadata_payload)
            index = LexicalIndexData.model_validate_json(index_payload)
            documents = _DOCUMENTS.validate_json(documents_payload)
        except ValueError as error:
            raise IndexingError("Blob lexical generation is invalid") from error
        if (
            metadata.generation_id != generation_id
            or metadata.format_version != _FORMAT_VERSION
            or metadata.index_sha256 != _sha256(index_payload)
            or metadata.documents_sha256 != _sha256(documents_payload)
            or metadata.document_count != len(documents)
        ):
            raise IndexingError("Blob lexical generation failed integrity validation")
        loaded = (metadata, index, documents)
        self._cache[key] = loaded
        while len(self._cache) > self._max_cached_generations:
            self._cache.popitem(last=False)
        return loaded

    async def _upload(self, name: str, payload: bytes) -> None:
        from azure.core.exceptions import ResourceExistsError

        try:
            await self._container.get_blob_client(name).upload_blob(
                payload, overwrite=False, metadata={"sha256": _sha256(payload)}
            )
        except ResourceExistsError:
            if await self._download(name) != payload:
                raise IndexingError(
                    "Immutable lexical blob already contains different data"
                ) from None

    async def _download(self, name: str) -> bytes:
        stream = await self._container.get_blob_client(name).download_blob()
        payload: bytes = await stream.readall()
        return payload

    @staticmethod
    def _prefix(workspace_id: str, generation_id: UUID) -> str:
        if not workspace_id or not all(
            character.isalnum() or character in "-_" for character in workspace_id
        ):
            raise ValueError("Invalid workspace identifier")
        return f"workspaces/{workspace_id}/lexical-generations/{generation_id}"


def _build_index(documents: Sequence[NormalizedDocument]) -> LexicalIndexData:
    term_frequencies: list[dict[str, int]] = []
    title_term_frequencies: list[dict[str, int]] = []
    document_lengths: list[int] = []
    document_frequencies: Counter[str] = Counter()
    for document in documents:
        body = Counter(tokenize_lexical(document.content))
        title = Counter(tokenize_lexical(document.title or ""))
        term_frequencies.append(dict(body))
        title_term_frequencies.append(dict(title))
        document_lengths.append(max(1, sum(body.values())))
        document_frequencies.update(body.keys() | title.keys())
    return LexicalIndexData(
        average_document_length=sum(document_lengths) / len(document_lengths),
        document_lengths=document_lengths,
        term_frequencies=term_frequencies,
        title_term_frequencies=title_term_frequencies,
        document_frequencies=dict(document_frequencies),
    )


def _score(
    index: LexicalIndexData,
    position: int,
    query_terms: tuple[str, ...],
    title_boost: float,
    k1: float,
    b: float,
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
        denominator = weighted_frequency + k1 * (1 - b + b * length / index.average_document_length)
        score += inverse_document_frequency * (weighted_frequency * (k1 + 1) / denominator)
    return score


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()

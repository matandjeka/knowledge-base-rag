"""Vector-store contract and durable workspace-scoped FAISS adapter."""

import asyncio
import hashlib
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

import faiss
import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.core.exceptions import IndexingError, IndexNotFoundError
from app.models import NormalizedDocument

_DOCUMENTS_ADAPTER = TypeAdapter(list[NormalizedDocument])


class VectorIndexMetadata(BaseModel):
    """Compatibility and integrity metadata for one immutable index generation."""

    model_config = ConfigDict(extra="forbid")

    generation_id: UUID
    model_name: str
    dimension: int = Field(ge=1)
    normalized: bool
    document_count: int = Field(ge=1)
    index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    documents_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class VectorSearchMatch(BaseModel):
    """One canonical document and its raw vector similarity."""

    model_config = ConfigDict(extra="forbid")

    document: NormalizedDocument
    score: float = Field(ge=-1, le=1)


class VectorStore(Protocol):
    """Persist and search workspace-isolated vector generations."""

    async def rebuild(
        self,
        workspace_id: str,
        documents: Sequence[NormalizedDocument],
        vectors: NDArray[np.float32],
        *,
        model_name: str,
        dimension: int,
    ) -> VectorIndexMetadata:
        """Atomically replace one workspace index generation."""
        ...

    async def prepare(
        self,
        workspace_id: str,
        documents: Sequence[NormalizedDocument],
        vectors: NDArray[np.float32],
        *,
        model_name: str,
        dimension: int,
    ) -> VectorIndexMetadata:
        """Write an immutable generation without making it current."""
        ...

    async def activate(self, workspace_id: str, generation_id: UUID) -> None:
        """Atomically make a prepared generation current."""
        ...

    async def search(
        self,
        workspace_id: str,
        query: NDArray[np.float32],
        *,
        top_k: int,
        source_ids: frozenset[UUID],
        model_name: str,
        dimension: int,
    ) -> tuple[VectorSearchMatch, ...]:
        """Search the current compatible workspace generation."""
        ...


class FaissVectorStore:
    """Store immutable FAISS generations beneath a controlled local root."""

    def __init__(self, root: Path) -> None:
        self._root = (root / "vector-indexes" / "workspaces").resolve()
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def rebuild(
        self,
        workspace_id: str,
        documents: Sequence[NormalizedDocument],
        vectors: NDArray[np.float32],
        *,
        model_name: str,
        dimension: int,
    ) -> VectorIndexMetadata:
        metadata = await self.prepare(
            workspace_id, documents, vectors, model_name=model_name, dimension=dimension
        )
        await self.activate(workspace_id, metadata.generation_id)
        return metadata

    async def prepare(
        self,
        workspace_id: str,
        documents: Sequence[NormalizedDocument],
        vectors: NDArray[np.float32],
        *,
        model_name: str,
        dimension: int,
    ) -> VectorIndexMetadata:
        if not documents:
            raise IndexingError("Cannot build a vector index without documents")
        if vectors.shape != (len(documents), dimension):
            raise IndexingError("Document and embedding dimensions do not match")
        if not np.isfinite(vectors).all():
            raise IndexingError("Vector index contains non-finite values")
        async with self._locks[workspace_id]:
            return await asyncio.to_thread(
                self._write_generation,
                workspace_id,
                list(documents),
                np.ascontiguousarray(vectors, dtype=np.float32),
                model_name,
                dimension,
            )

    async def activate(self, workspace_id: str, generation_id: UUID) -> None:
        async with self._locks[workspace_id]:
            await asyncio.to_thread(self._activate_generation, workspace_id, generation_id)

    async def search(
        self,
        workspace_id: str,
        query: NDArray[np.float32],
        *,
        top_k: int,
        source_ids: frozenset[UUID],
        model_name: str,
        dimension: int,
    ) -> tuple[VectorSearchMatch, ...]:
        if query.shape != (dimension,):
            raise IndexingError("Query embedding dimension does not match the vector index")
        async with self._locks[workspace_id]:
            return await asyncio.to_thread(
                self._search_sync,
                workspace_id,
                np.ascontiguousarray(query, dtype=np.float32),
                top_k,
                source_ids,
                model_name,
                dimension,
            )

    def _write_generation(
        self,
        workspace_id: str,
        documents: list[NormalizedDocument],
        vectors: NDArray[np.float32],
        model_name: str,
        dimension: int,
    ) -> VectorIndexMetadata:
        workspace_directory = self._workspace_directory(workspace_id)
        generation_id = uuid4()
        generation_directory = workspace_directory / "generations" / str(generation_id)
        generation_directory.mkdir(parents=True, exist_ok=False)
        index_path = generation_directory / "index.faiss"
        documents_path = generation_directory / "documents.json"
        metadata_path = generation_directory / "metadata.json"

        index = faiss.IndexFlatIP(dimension)
        index.add(vectors)
        faiss.write_index(index, str(index_path))
        documents_payload = _DOCUMENTS_ADAPTER.dump_json(documents)
        documents_path.write_bytes(documents_payload)
        metadata = VectorIndexMetadata(
            generation_id=generation_id,
            model_name=model_name,
            dimension=dimension,
            normalized=True,
            document_count=len(documents),
            index_sha256=_sha256(index_path.read_bytes()),
            documents_sha256=_sha256(documents_payload),
        )
        metadata_path.write_text(metadata.model_dump_json(indent=2))
        return metadata

    def _activate_generation(self, workspace_id: str, generation_id: UUID) -> None:
        workspace_directory = self._workspace_directory(workspace_id)
        generation = workspace_directory / "generations" / str(generation_id)
        if not generation.is_dir():
            raise IndexingError("Prepared vector-index generation does not exist")
        current = workspace_directory / "CURRENT"
        temporary = workspace_directory / "CURRENT.tmp"
        temporary.write_text(str(generation_id))
        temporary.replace(current)

    def _search_sync(
        self,
        workspace_id: str,
        query: NDArray[np.float32],
        top_k: int,
        source_ids: frozenset[UUID],
        model_name: str,
        dimension: int,
    ) -> tuple[VectorSearchMatch, ...]:
        workspace_directory = self._workspace_directory(workspace_id)
        current = workspace_directory / "CURRENT"
        if not current.is_file():
            raise IndexNotFoundError("No vector index exists for this workspace")
        try:
            generation_id = UUID(current.read_text().strip())
        except (ValueError, OSError) as error:
            raise IndexingError("The workspace vector-index pointer is invalid") from error
        directory = workspace_directory / "generations" / str(generation_id)
        index_path = directory / "index.faiss"
        documents_path = directory / "documents.json"
        metadata_path = directory / "metadata.json"
        try:
            metadata = VectorIndexMetadata.model_validate_json(metadata_path.read_text())
            index_payload = index_path.read_bytes()
            documents_payload = documents_path.read_bytes()
        except (OSError, ValueError) as error:
            raise IndexingError("The current vector-index generation is unreadable") from error
        if metadata.generation_id != generation_id:
            raise IndexingError("Vector-index generation metadata does not match its pointer")
        if metadata.model_name != model_name or metadata.dimension != dimension:
            raise IndexingError("Vector index is incompatible with the configured embedding model")
        if _sha256(index_payload) != metadata.index_sha256:
            raise IndexingError("Vector-index checksum validation failed")
        if _sha256(documents_payload) != metadata.documents_sha256:
            raise IndexingError("Vector document-mapping checksum validation failed")
        try:
            documents = _DOCUMENTS_ADAPTER.validate_json(documents_payload)
            index = faiss.read_index(str(index_path))
        except (ValueError, RuntimeError) as error:
            raise IndexingError("The current vector-index generation is invalid") from error
        if index.ntotal != len(documents) or len(documents) != metadata.document_count:
            raise IndexingError("Vector index and document mapping are out of sync")
        search_count = len(documents)
        scores, positions = index.search(query.reshape(1, -1), search_count)
        matches: list[VectorSearchMatch] = []
        for score, position in zip(scores[0], positions[0], strict=True):
            if position < 0:
                continue
            document = documents[int(position)]
            if source_ids and document.source_id not in source_ids:
                continue
            matches.append(VectorSearchMatch(document=document, score=float(score)))
            if len(matches) == top_k:
                break
        return tuple(matches)

    def _workspace_directory(self, workspace_id: str) -> Path:
        if not workspace_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in workspace_id
        ):
            raise ValueError("Invalid workspace identifier")
        directory = (self._root / workspace_id).resolve()
        if not directory.is_relative_to(self._root):
            raise ValueError("Vector index path escapes the configured data directory")
        return directory


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()

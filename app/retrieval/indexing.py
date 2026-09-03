"""Workspace vector-index orchestration over persisted source documents."""

from typing import Protocol
from uuid import UUID

from app.core.exceptions import IndexingError
from app.models import NormalizedDocument, SourceStatus
from app.repositories import SourceRepository
from app.retrieval.embedding import EmbeddingService
from app.retrieval.sentence_window import build_sentence_window_documents
from app.retrieval.vector_store import (
    VectorIndexKind,
    VectorIndexMetadata,
    VectorIndexPayload,
    VectorStore,
)
from app.storage import SourceStorage


class SourceIndexer(Protocol):
    """Ingestion-facing boundary for rebuilding a workspace vector index."""

    async def rebuild(self, workspace_id: str, source_id: UUID) -> VectorIndexMetadata:
        """Rebuild a workspace while including the source currently being ingested."""
        ...

    async def prepare(self, workspace_id: str, source_id: UUID) -> VectorIndexMetadata:
        """Prepare a workspace generation without activating it."""
        ...

    async def activate(self, workspace_id: str, generation_id: UUID) -> None:
        """Activate a previously prepared generation."""
        ...


class VectorIndexingService:
    """Collect persisted documents, embed them, and rebuild one workspace index."""

    def __init__(
        self,
        repository: SourceRepository,
        storage: SourceStorage,
        embeddings: EmbeddingService,
        vector_store: VectorStore,
        *,
        sentence_window_radius: int = 2,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._embeddings = embeddings
        self._vector_store = vector_store
        self._sentence_window_radius = sentence_window_radius

    async def rebuild(self, workspace_id: str, source_id: UUID) -> VectorIndexMetadata:
        """Prepare and activate a workspace generation."""
        metadata = await self.prepare(workspace_id, source_id)
        await self.activate(workspace_id, metadata.generation_id)
        return metadata

    async def prepare(self, workspace_id: str, source_id: UUID) -> VectorIndexMetadata:
        """Build from persisted ready sources plus the source being ingested."""
        target = await self._repository.get(workspace_id, source_id)
        persisted = {
            source.source_id: source for source in await self._storage.list_sources(workspace_id)
        }
        persisted[target.source_id] = target
        sources = sorted(
            persisted.values(), key=lambda source: (source.created_at, str(source.source_id))
        )
        documents: list[NormalizedDocument] = []
        for source in sources:
            if source.status is SourceStatus.READY or source.source_id == target.source_id:
                documents.extend(await self._storage.load_documents(workspace_id, source.source_id))
        if not documents:
            raise IndexingError("No persisted documents are available for vector indexing")
        sentence_documents = build_sentence_window_documents(
            documents, radius=self._sentence_window_radius
        )
        if not sentence_documents:
            raise IndexingError("No sentences are available for sentence-window indexing")
        vectors = await self._embeddings.embed_documents(
            [document.content for document in documents]
        )
        sentence_vectors = await self._embeddings.embed_documents(
            [document.content for document in sentence_documents]
        )
        generation = await self._vector_store.prepare_bundle(
            workspace_id,
            {
                VectorIndexKind.VECTOR: VectorIndexPayload(documents, vectors),
                VectorIndexKind.SENTENCE_WINDOW: VectorIndexPayload(
                    sentence_documents, sentence_vectors
                ),
            },
            model_name=self._embeddings.model_name,
            dimension=self._embeddings.dimension,
        )
        return generation.indexes[VectorIndexKind.VECTOR]

    async def activate(self, workspace_id: str, generation_id: UUID) -> None:
        """Atomically publish a prepared generation."""
        await self._vector_store.activate(workspace_id, generation_id)

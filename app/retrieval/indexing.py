"""Workspace vector-index orchestration over persisted source documents."""

from typing import Protocol
from uuid import UUID, uuid4

from app.core.exceptions import IndexingError, IndexNotFoundError
from app.models import NormalizedDocument, SourceStatus
from app.persistence import GenerationKind, PersistenceRepository
from app.repositories import SourceRepository
from app.retrieval.embedding import EmbeddingService
from app.retrieval.lexical import LexicalStore
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
        lexical_store: LexicalStore | None = None,
        persistence_repository: PersistenceRepository | None = None,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._embeddings = embeddings
        self._vector_store = vector_store
        self._sentence_window_radius = sentence_window_radius
        self._lexical_store = lexical_store
        self._persistence_repository = persistence_repository

    async def rebuild(self, workspace_id: str, source_id: UUID) -> VectorIndexMetadata:
        """Prepare and activate a workspace generation."""
        metadata = await self.prepare(workspace_id, source_id)
        await self.activate(workspace_id, metadata.generation_id)
        return metadata

    async def prepare(self, workspace_id: str, source_id: UUID) -> VectorIndexMetadata:
        """Build from persisted ready sources plus the source being ingested."""
        target = await self._repository.get(workspace_id, source_id)
        persisted = {
            source.source_id: source for source in await self._repository.list(workspace_id)
        }
        if self._persistence_repository is None:
            persisted.update(
                {
                    source.source_id: source
                    for source in await self._storage.list_sources(workspace_id)
                }
            )
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
        persistence = self._persistence_repository
        generation_id = uuid4() if persistence is not None else None
        operation = None
        if generation_id is not None:
            assert persistence is not None
            operation = await persistence.begin_operation(
                workspace_id, generation_id, GenerationKind.RETRIEVAL
            )
        try:
            payloads = {
                VectorIndexKind.VECTOR: VectorIndexPayload(documents, vectors),
                VectorIndexKind.SENTENCE_WINDOW: VectorIndexPayload(
                    sentence_documents, sentence_vectors
                ),
            }
            if generation_id is None:
                generation = await self._vector_store.prepare_bundle(
                    workspace_id,
                    payloads,
                    model_name=self._embeddings.model_name,
                    dimension=self._embeddings.dimension,
                )
            else:
                generation = await self._vector_store.prepare_bundle(
                    workspace_id,
                    payloads,
                    model_name=self._embeddings.model_name,
                    dimension=self._embeddings.dimension,
                    generation_id=generation_id,
                )
            if operation is not None:
                assert persistence is not None
                await persistence.mark_prepared(operation.operation_id, "vector")
            if self._lexical_store is not None:
                await self._lexical_store.prepare(
                    workspace_id,
                    documents,
                    generation_id=generation.generation_id,
                )
                if operation is not None:
                    assert persistence is not None
                    await persistence.mark_prepared(operation.operation_id, "lexical")
        except Exception as error:
            if operation is not None:
                assert persistence is not None
                await persistence.fail(operation.operation_id, type(error).__name__.lower())
            raise
        return generation.indexes[VectorIndexKind.VECTOR]

    async def activate(self, workspace_id: str, generation_id: UUID) -> None:
        """Publish matching indexes and roll back lexical state on vector failure."""
        if self._persistence_repository is not None:
            operation = await self._persistence_repository.operation_for_generation(
                workspace_id, generation_id, GenerationKind.RETRIEVAL
            )
            try:
                await self._vector_store.activate(workspace_id, generation_id)
                required = {"vector"}
                if self._lexical_store is not None:
                    await self._lexical_store.activate(workspace_id, generation_id)
                    required.add("lexical")
                await self._persistence_repository.publish(
                    operation.operation_id, frozenset(required)
                )
            except Exception as error:
                await self._persistence_repository.fail(
                    operation.operation_id, type(error).__name__.lower()
                )
                raise
            return
        if self._lexical_store is None:
            await self._vector_store.activate(workspace_id, generation_id)
            return
        try:
            previous_lexical = await self._lexical_store.active_generation(workspace_id)
        except IndexNotFoundError:
            previous_lexical = None
        await self._lexical_store.activate(workspace_id, generation_id)
        try:
            await self._vector_store.activate(workspace_id, generation_id)
        except Exception:
            try:
                active_vector = await self._vector_store.active_generation(workspace_id)
            except Exception:
                active_vector = None
            if active_vector == generation_id:
                return
            await self._lexical_store.restore_activation(workspace_id, previous_lexical)
            raise

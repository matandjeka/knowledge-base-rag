"""Shared deterministic test doubles for cross-cutting application services."""

from uuid import UUID, uuid4

from app.retrieval.vector_store import VectorIndexMetadata


class RecordingSourceIndexer:
    """Record ingestion-triggered rebuilds without loading an embedding model."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, UUID]] = []
        self._fail = fail

    async def rebuild(self, workspace_id: str, source_id: UUID) -> VectorIndexMetadata:
        metadata = await self.prepare(workspace_id, source_id)
        await self.activate(workspace_id, metadata.generation_id)
        return metadata

    async def prepare(self, workspace_id: str, source_id: UUID) -> VectorIndexMetadata:
        self.calls.append((workspace_id, source_id))
        if self._fail:
            raise OSError("vector index unavailable")
        return VectorIndexMetadata(
            generation_id=uuid4(),
            model_name="test-embedding",
            dimension=3,
            normalized=True,
            document_count=1,
            index_sha256="0" * 64,
            documents_sha256="0" * 64,
        )

    async def activate(self, workspace_id: str, generation_id: UUID) -> None:
        del workspace_id, generation_id

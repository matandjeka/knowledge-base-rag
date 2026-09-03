"""Full-workspace knowledge-graph extraction and atomic indexing orchestration."""

from collections.abc import Sequence
from uuid import uuid4

from app.core.exceptions import IndexingError
from app.graph.extraction import GRAPH_SCHEMA_VERSION, GraphExtractor, resolve_graph
from app.graph.store import GraphStore
from app.models import GraphIndexResult, GraphSnapshot, NormalizedDocument, SourceStatus
from app.storage import SourceStorage


class GraphIndexingService:
    """Rebuild a complete graph from every persisted ready source in a workspace."""

    def __init__(
        self,
        storage: SourceStorage,
        extractor: GraphExtractor,
        graph_store: GraphStore,
        *,
        batch_size: int,
        max_batch_characters: int,
    ) -> None:
        self._storage = storage
        self._extractor = extractor
        self._graph_store = graph_store
        self._batch_size = batch_size
        self._max_batch_characters = max_batch_characters

    async def rebuild(self, workspace_id: str) -> GraphIndexResult:
        """Extract, resolve, stage, validate, and activate one complete workspace graph."""
        sources = [
            source
            for source in await self._storage.list_sources(workspace_id)
            if source.status is SourceStatus.READY
        ]
        if not sources:
            raise IndexingError("No persisted ready sources are available for graph indexing")
        documents: list[NormalizedDocument] = []
        for source in sources:
            documents.extend(await self._storage.load_documents(workspace_id, source.source_id))
        if not documents:
            raise IndexingError("No persisted documents are available for graph indexing")

        extractions = []
        for batch in self._batches(documents):
            extractions.extend(await self._extractor.extract(batch))
        entities, relationships = resolve_graph(workspace_id, documents, extractions)
        snapshot = GraphSnapshot(
            generation_id=uuid4(),
            workspace_id=workspace_id,
            schema_version=GRAPH_SCHEMA_VERSION,
            extractor_model=self._extractor.model_name,
            prompt_version=self._extractor.prompt_version,
            source_ids=[source.source_id for source in sources],
            document_count=len(documents),
            entities=entities,
            relationships=relationships,
        )
        metadata = await self._graph_store.prepare(snapshot)
        await self._graph_store.activate(workspace_id, metadata.generation_id)
        return GraphIndexResult(
            workspace_id=workspace_id,
            generation_id=metadata.generation_id,
            source_count=metadata.source_count,
            document_count=metadata.document_count,
            entity_count=metadata.entity_count,
            relationship_count=metadata.relationship_count,
        )

    def _batches(self, documents: Sequence[NormalizedDocument]) -> list[list[NormalizedDocument]]:
        batches: list[list[NormalizedDocument]] = []
        current: list[NormalizedDocument] = []
        characters = 0
        for document in documents:
            if current and (
                len(current) == self._batch_size
                or characters + len(document.content) > self._max_batch_characters
            ):
                batches.append(current)
                current = []
                characters = 0
            current.append(document)
            characters += len(document.content)
        if current:
            batches.append(current)
        return batches

"""Baseline workspace-scoped vector retriever."""

from uuid import UUID

from app.models import Evidence
from app.retrieval.embedding import EmbeddingService
from app.retrieval.vector_store import VectorStore


class VectorRetriever:
    """Embed one query and map FAISS matches into canonical evidence."""

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
        """Return ordered evidence whose cosine score clears the configured threshold."""
        query_vector = await self._embeddings.embed_query(query)
        matches = await self._vector_store.search(
            workspace_id,
            query_vector,
            top_k=top_k,
            source_ids=source_ids,
            model_name=self._embeddings.model_name,
            dimension=self._embeddings.dimension,
        )
        evidence: list[Evidence] = []
        for match in matches:
            raw_score = min(1.0, max(-1.0, match.score))
            if raw_score < min_similarity:
                continue
            document = match.document
            evidence.append(
                Evidence(
                    retriever="vector",
                    content=document.content,
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
                    },
                )
            )
        return evidence

"""Baseline retrieval, citation, and extractive-answer orchestration."""

from app.citations.builder import build_citations
from app.core.exceptions import RetrievalError
from app.generation.extractive import Generator
from app.models import QueryRequest, QueryResponse
from app.repositories import SourceRepository
from app.retrieval.vector import VectorRetriever


class QueryService:
    """Validate workspace filters and produce one grounded baseline response."""

    def __init__(
        self,
        repository: SourceRepository,
        retriever: VectorRetriever,
        generator: Generator,
        *,
        default_top_k: int,
        max_top_k: int,
        min_similarity: float,
    ) -> None:
        self._repository = repository
        self._retriever = retriever
        self._generator = generator
        self._default_top_k = default_top_k
        self._max_top_k = max_top_k
        self._min_similarity = min_similarity

    async def query(self, request: QueryRequest) -> QueryResponse:
        """Retrieve evidence and return citations or an insufficient-evidence response."""
        top_k = request.top_k or self._default_top_k
        if top_k > self._max_top_k:
            raise RetrievalError(f"top_k cannot exceed {self._max_top_k}")
        for source_id in request.source_ids:
            await self._repository.get(request.workspace_id, source_id)
        evidence = await self._retriever.retrieve(
            request.workspace_id,
            request.question,
            top_k=top_k,
            source_ids=frozenset(request.source_ids),
            min_similarity=self._min_similarity,
        )
        citations = build_citations(evidence)
        answer = await self._generator.generate(request.question, evidence, citations)
        return QueryResponse(
            answer=answer,
            citations=citations,
            evidence=evidence,
            insufficient_evidence=not evidence,
        )

"""Baseline retrieval, citation, and extractive-answer orchestration."""

from app.citations.builder import build_citations
from app.core.exceptions import GraphIndexNotFoundError, RetrievalError
from app.generation.extractive import Generator
from app.models import Evidence, FusionStrategy, QueryRequest, QueryResponse, RetrievalMode
from app.repositories import SourceRepository
from app.retrieval.base import Retriever
from app.retrieval.fusion import DEFAULT_FUSION_RETRIEVERS, FusionRetriever


class QueryService:
    """Validate workspace filters and produce one grounded baseline response."""

    def __init__(
        self,
        repository: SourceRepository,
        retriever: Retriever,
        sentence_window_retriever: Retriever,
        generator: Generator,
        *,
        default_top_k: int,
        max_top_k: int,
        min_similarity: float,
        graph_retriever: Retriever | None = None,
        lexical_retriever: Retriever | None = None,
        lexical_min_score: float = 0.0,
        fusion_retriever: FusionRetriever | None = None,
    ) -> None:
        self._repository = repository
        self._retriever = retriever
        self._sentence_window_retriever = sentence_window_retriever
        self._generator = generator
        self._default_top_k = default_top_k
        self._max_top_k = max_top_k
        self._min_similarity = min_similarity
        self._graph_retriever = graph_retriever
        self._lexical_retriever = lexical_retriever
        self._lexical_min_score = lexical_min_score
        self._fusion_retriever = fusion_retriever

    async def query(self, request: QueryRequest) -> QueryResponse:
        """Retrieve evidence and return citations or an insufficient-evidence response."""
        top_k = request.top_k or self._default_top_k
        if top_k > self._max_top_k:
            raise RetrievalError(f"top_k cannot exceed {self._max_top_k}")
        for source_id in request.source_ids:
            await self._repository.get(request.workspace_id, source_id)
        if request.retrieval_mode is RetrievalMode.FUSION:
            if self._fusion_retriever is None:
                raise RetrievalError("Fusion retrieval is not configured")
            evidence = await self._fusion_retriever.retrieve(
                request.workspace_id,
                request.question,
                top_k=top_k,
                source_ids=frozenset(request.source_ids),
                modes=tuple(request.fusion_retrievers or DEFAULT_FUSION_RETRIEVERS),
                strategy=request.fusion_strategy or FusionStrategy.RRF,
            )
            return await self._build_response(request.question, evidence)
        if request.retrieval_mode is RetrievalMode.GRAPH:
            if self._graph_retriever is None:
                raise GraphIndexNotFoundError("Graph retrieval is not configured")
            retriever = self._graph_retriever
            minimum_score = self._min_similarity
        elif request.retrieval_mode is RetrievalMode.LEXICAL:
            if self._lexical_retriever is None:
                raise RetrievalError("Lexical retrieval is not configured")
            retriever = self._lexical_retriever
            minimum_score = self._lexical_min_score
        elif request.retrieval_mode is RetrievalMode.SENTENCE_WINDOW:
            retriever = self._sentence_window_retriever
            minimum_score = self._min_similarity
        else:
            retriever = self._retriever
            minimum_score = self._min_similarity
        evidence = await retriever.retrieve(
            request.workspace_id,
            request.question,
            top_k=top_k,
            source_ids=frozenset(request.source_ids),
            min_similarity=minimum_score,
        )
        return await self._build_response(request.question, evidence)

    async def _build_response(self, question: str, evidence: list[Evidence]) -> QueryResponse:
        """Build citations and a grounded answer from ordered canonical evidence."""
        citations = build_citations(evidence)
        answer = await self._generator.generate(question, evidence, citations)
        return QueryResponse(
            answer=answer,
            citations=citations,
            evidence=evidence,
            insufficient_evidence=not evidence,
        )

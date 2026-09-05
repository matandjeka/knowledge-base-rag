"""Routed retrieval, citation, and grounded-answer orchestration."""

import logging

from app.citations.builder import build_citations
from app.core.exceptions import GraphIndexNotFoundError, RetrievalError
from app.generation.extractive import Generator
from app.models import (
    Evidence,
    FusionStrategy,
    QueryRequest,
    QueryResponse,
    RetrievalMode,
    RoutingIntent,
    RoutingKind,
    RoutingTrace,
)
from app.repositories import SourceRepository
from app.reranking.service import RerankingService
from app.retrieval.base import Retriever
from app.retrieval.fusion import DEFAULT_FUSION_RETRIEVERS, FusionRetriever
from app.routing import RuleBasedQueryRouter

logger = logging.getLogger(__name__)


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
        reranking_service: RerankingService | None = None,
        reranking_candidate_pool_size: int = 30,
        database_retriever: Retriever | None = None,
        query_router: RuleBasedQueryRouter | None = None,
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
        self._reranking_service = reranking_service
        self._reranking_candidate_pool_size = reranking_candidate_pool_size
        self._database_retriever = database_retriever
        self._query_router = query_router

    async def query(self, request: QueryRequest) -> QueryResponse:
        """Retrieve evidence and return citations or an insufficient-evidence response."""
        top_k = request.top_k or self._default_top_k
        if top_k > self._max_top_k:
            raise RetrievalError(f"top_k cannot exceed {self._max_top_k}")
        for source_id in request.source_ids:
            await self._repository.get(request.workspace_id, source_id)
        if request.retrieval_mode is RetrievalMode.AUTO:
            return await self._query_automatically(request, top_k)
        if request.retrieval_mode is RetrievalMode.FUSION:
            if self._fusion_retriever is None:
                raise RetrievalError("Fusion retrieval is not configured")
            candidate_top_k = (
                max(top_k, self._reranking_candidate_pool_size) if request.rerank else top_k
            )
            evidence = await self._fusion_retriever.retrieve(
                request.workspace_id,
                request.question,
                top_k=candidate_top_k,
                source_ids=frozenset(request.source_ids),
                modes=tuple(request.fusion_retrievers or DEFAULT_FUSION_RETRIEVERS),
                strategy=request.fusion_strategy or FusionStrategy.RRF,
            )
            if request.rerank:
                if self._reranking_service is None:
                    raise RetrievalError("Re-ranking is not configured")
                evidence = await self._reranking_service.rerank(
                    request.question, evidence, top_k=top_k
                )
            trace = self._override_trace(
                request, tuple(request.fusion_retrievers or DEFAULT_FUSION_RETRIEVERS)
            )
            self._log_route(request, trace)
            return await self._build_response(request.question, evidence, trace)
        if request.retrieval_mode is RetrievalMode.SQL:
            if self._database_retriever is None:
                raise RetrievalError(
                    "SQL retrieval requires OPENAI_API_KEY and SQL_GENERATION_MODEL"
                )
            retriever = self._database_retriever
            minimum_score = 0.0
        elif request.retrieval_mode is RetrievalMode.GRAPH:
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
        trace = self._override_trace(request, (request.retrieval_mode,))
        self._log_route(request, trace)
        return await self._build_response(request.question, evidence, trace)

    async def _query_automatically(self, request: QueryRequest, top_k: int) -> QueryResponse:
        if self._query_router is None or self._fusion_retriever is None:
            raise RetrievalError("Automatic query routing is not configured")
        sources = await self._repository.list(request.workspace_id)
        trace = self._query_router.route(
            request.question,
            sources,
            request.source_ids,
            self._available_retrievers(),
        )
        try:
            evidence = await self._execute_auto_plan(request, trace, top_k)
        except GraphIndexNotFoundError:
            if RetrievalMode.GRAPH not in trace.selected_retrievers:
                raise
            trace = trace.model_copy(
                update={
                    "intent": RoutingIntent.DEFAULT,
                    "selected_retrievers": list(DEFAULT_FUSION_RETRIEVERS),
                    "selected_source_ids": list(request.source_ids),
                    "fallback_reason": "graph_index_unavailable",
                }
            )
            evidence = await self._fusion_retriever.retrieve(
                request.workspace_id,
                request.question,
                top_k=top_k,
                source_ids=frozenset(request.source_ids),
                modes=DEFAULT_FUSION_RETRIEVERS,
                strategy=FusionStrategy.RRF,
            )
        self._log_route(request, trace)
        return await self._build_response(request.question, evidence, trace)

    async def _execute_auto_plan(
        self, request: QueryRequest, trace: RoutingTrace, top_k: int
    ) -> list[Evidence]:
        modes = tuple(trace.selected_retrievers)
        source_ids = frozenset(trace.selected_source_ids)
        if modes == (RetrievalMode.SQL,):
            if self._database_retriever is None:
                raise RetrievalError("SQL retrieval is not configured")
            return await self._database_retriever.retrieve(
                request.workspace_id,
                request.question,
                top_k=top_k,
                source_ids=source_ids,
                min_similarity=0.0,
            )
        if self._fusion_retriever is None:
            raise RetrievalError("Fusion retrieval is not configured")
        return await self._fusion_retriever.retrieve(
            request.workspace_id,
            request.question,
            top_k=top_k,
            source_ids=source_ids,
            modes=modes,
            strategy=FusionStrategy.RRF,
        )

    def _available_retrievers(self) -> frozenset[RetrievalMode]:
        modes = {RetrievalMode.VECTOR, RetrievalMode.SENTENCE_WINDOW}
        if self._graph_retriever is not None:
            modes.add(RetrievalMode.GRAPH)
        if self._lexical_retriever is not None:
            modes.add(RetrievalMode.LEXICAL)
        if self._database_retriever is not None:
            modes.add(RetrievalMode.SQL)
        return frozenset(modes)

    def _override_trace(
        self, request: QueryRequest, modes: tuple[RetrievalMode, ...]
    ) -> RoutingTrace:
        intent_by_mode = {
            RetrievalMode.SQL: RoutingIntent.SQL,
            RetrievalMode.GRAPH: RoutingIntent.GRAPH,
            RetrievalMode.SENTENCE_WINDOW: RoutingIntent.SENTENCE_WINDOW,
            RetrievalMode.LEXICAL: RoutingIntent.LEXICAL,
        }
        return RoutingTrace(
            kind=RoutingKind.OVERRIDE,
            intent=intent_by_mode.get(request.retrieval_mode, RoutingIntent.DEFAULT),
            confidence=1.0,
            selected_retrievers=list(modes),
            selected_source_ids=list(request.source_ids),
            routing_latency_ms=0.0,
        )

    def _log_route(self, request: QueryRequest, trace: RoutingTrace) -> None:
        logger.info(
            "Query route selected",
            extra={
                "workspace_id": request.workspace_id,
                "retrieval_mode": trace.kind.value,
                "routing_intent": trace.intent.value,
                "selected_retrievers": [mode.value for mode in trace.selected_retrievers],
                "routing_confidence": trace.confidence,
                "routing_fallback": trace.fallback_reason,
            },
        )

    async def _build_response(
        self,
        question: str,
        evidence: list[Evidence],
        routing_trace: RoutingTrace | None = None,
    ) -> QueryResponse:
        """Build citations and a grounded answer from ordered canonical evidence."""
        citations = build_citations(evidence)
        answer = await self._generator.generate(question, evidence, citations)
        return QueryResponse(
            answer=answer,
            citations=citations,
            evidence=evidence,
            insufficient_evidence=not evidence,
            routing_trace=routing_trace,
        )

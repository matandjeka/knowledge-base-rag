"""Executable deterministic corpus, QueryService evaluator, and optional faithfulness judge."""

import re
import time
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from app.evaluation.framework import (
    CaseObservation,
    EvaluationTier,
    ExpectedEvidence,
    GoldenBenchmark,
    GoldenCase,
    ObservedAnswerSegment,
    RetrievalConfiguration,
)
from app.generation.extractive import ExtractiveGenerator
from app.models import (
    Citation,
    DatabaseCitationLocator,
    Evidence,
    FusionStrategy,
    QueryRequest,
    RetrievalMode,
    Source,
    SourceConfig,
    SourceStatus,
    SourceType,
)
from app.repositories import InMemorySourceRepository
from app.retrieval.fusion import FusionRetriever
from app.retrieval.query_service import QueryService
from app.routing import RuleBasedQueryRouter

_WORKSPACE_ID = "evaluation"
_NORMALIZE = re.compile(r"\s+")
_JUDGE_PROMPT = """Judge whether the answer segment is fully supported by the cited excerpts.
Return faithful=true only when every factual statement follows from the excerpts. Treat any
unsupported addition or contradiction as unfaithful. Do not use outside knowledge."""


class FaithfulnessJudgment(BaseModel):
    """Strict optional semantic judgment for one observed answer segment."""

    model_config = ConfigDict(extra="forbid")

    faithful: bool
    rationale: str = Field(min_length=1, max_length=1000)


class FaithfulnessJudge(Protocol):
    """Optional live semantic judge independent from deterministic deployment metrics."""

    model_name: str

    async def judge(self, segment: str, cited_excerpts: list[str]) -> FaithfulnessJudgment:
        """Return a structured support judgment for one answer segment."""
        ...


class OpenAIFaithfulnessJudge:
    """OpenAI Responses structured-output faithfulness judge."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        timeout_seconds: float,
        max_retries: int,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self.model_name = model_name
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._client = client

    async def judge(self, segment: str, cited_excerpts: list[str]) -> FaithfulnessJudgment:
        payload = f"Answer segment:\n{segment}\n\nCited excerpts:\n" + "\n---\n".join(
            cited_excerpts
        )
        if self._client is not None:
            return await self._parse(self._client, payload)
        async with self._create_client() as client:
            return await self._parse(client, payload)

    async def _parse(self, client: Any, payload: str) -> FaithfulnessJudgment:
        response = await client.responses.parse(
            model=self.model_name,
            input=[
                {"role": "system", "content": _JUDGE_PROMPT},
                {"role": "user", "content": payload},
            ],
            text_format=FaithfulnessJudgment,
        )
        parsed = response.output_parsed
        if not isinstance(parsed, FaithfulnessJudgment):
            raise ValueError("Faithfulness judge returned no structured output")
        return parsed

    def _create_client(self) -> Any:
        from openai import AsyncOpenAI

        return AsyncOpenAI(
            api_key=self._api_key,
            timeout=self._timeout_seconds,
            max_retries=self._max_retries,
        )


class QueryServiceEvaluator:
    """Derive normalized observations by executing the application query service."""

    def __init__(
        self,
        service: QueryService,
        *,
        workspace_id: str = _WORKSPACE_ID,
        judge: FaithfulnessJudge | None = None,
    ) -> None:
        self._service = service
        self._workspace_id = workspace_id
        self._judge = judge

    @property
    def judge_model(self) -> str | None:
        return self._judge.model_name if self._judge is not None else None

    async def __call__(
        self,
        case: GoldenCase,
        configuration: RetrievalConfiguration,
        tier: EvaluationTier,
    ) -> CaseObservation:
        del tier
        started = time.perf_counter()
        try:
            response = await self._service.query(
                _query_request(case, configuration, self._workspace_id)
            )
            expected_by_location = {
                (item.source_type, item.locator): item.label for item in case.expected_evidence
            }
            citation_labels = {
                citation.citation_id: expected_by_location.get(
                    (citation.source_type, _evaluation_locator(citation))
                )
                for citation in response.citations
            }
            labels_by_evidence: dict[UUID, list[str]] = {}
            for citation in response.citations:
                label = citation_labels[citation.citation_id]
                if label is not None:
                    labels_by_evidence.setdefault(citation.evidence_id, []).append(label)
            segments = [
                ObservedAnswerSegment(
                    text=segment.text,
                    cited_evidence_labels=[
                        label
                        for citation_id in segment.citation_ids
                        if (label := citation_labels.get(citation_id)) is not None
                    ],
                )
                for segment in response.citation_segments or []
            ]
            judge_faithful = await self._judge_segments(
                segments, response.citations, citation_labels
            )
            trace = response.routing_trace
            return CaseObservation(
                case_id=case.case_id,
                retrieved_evidence_labels=list(
                    dict.fromkeys(
                        label
                        for item in response.evidence
                        for label in labels_by_evidence.get(item.evidence_id, [])
                    )
                ),
                cited_evidence_labels=list(
                    dict.fromkeys(
                        label
                        for citation in response.citations
                        if (label := citation_labels.get(citation.citation_id)) is not None
                    )
                ),
                answer_segments=segments,
                routed_intent=trace.intent if trace else None,
                selected_retrievers=trace.selected_retrievers if trace else [],
                insufficient_evidence=response.insufficient_evidence,
                safe_sql_behavior=True if case.sql_safe_required else None,
                latency_seconds=time.perf_counter() - started,
                judge_faithful=judge_faithful,
            )
        except Exception as error:
            return CaseObservation(
                case_id=case.case_id,
                latency_seconds=time.perf_counter() - started,
                safe_sql_behavior=False if case.sql_safe_required else None,
                error_category=_error_category(error),
            )

    async def _judge_segments(
        self,
        segments: list[ObservedAnswerSegment],
        citations: list[Citation],
        citation_labels: dict[str, str | None],
    ) -> bool | None:
        if self._judge is None or not segments:
            return None
        excerpts_by_label = {
            label: citation.excerpt
            for citation in citations
            if (label := citation_labels.get(citation.citation_id)) is not None
        }
        results = []
        for segment in segments:
            excerpts = [
                excerpts_by_label[label]
                for label in segment.cited_evidence_labels
                if label in excerpts_by_label
            ]
            results.append((await self._judge.judge(segment.text, excerpts)).faithful)
        return all(results)


class DeterministicCorpusRetriever:
    """Query-matched in-memory corpus adapter used by the required CI tier."""

    def __init__(self, evidence: list[Evidence], mode: RetrievalMode) -> None:
        self._evidence = evidence
        self._mode = mode

    async def retrieve(
        self,
        workspace_id: str,
        query: str,
        *,
        top_k: int,
        source_ids: frozenset[UUID],
        min_similarity: float = 0.0,
    ) -> list[Evidence]:
        del workspace_id, min_similarity
        normalized = _normalize(query)
        candidates = [
            item
            for item in self._evidence
            if _normalize(str(item.metadata["evaluation_query"])) == normalized
            and (not source_ids or item.source_id in source_ids)
            and (self._mode is not RetrievalMode.SQL or item.source_type is SourceType.DATABASE)
        ]
        return [
            item.model_copy(
                update={"retriever": self._mode.value, "raw_score": 1.0, "normalized_score": 1.0},
                deep=True,
            )
            for item in candidates[:top_k]
        ]


async def build_deterministic_query_service(benchmark: GoldenBenchmark) -> QueryService:
    """Build a complete in-memory application pipeline from the committed corpus labels."""
    repository = InMemorySourceRepository()
    sources: dict[SourceType, Source] = {}
    for source_type in SourceType:
        source = Source(
            source_id=uuid5(NAMESPACE_URL, f"evaluation-source:{source_type.value}"),
            workspace_id=_WORKSPACE_ID,
            name=f"Evaluation {source_type.value}",
            config=SourceConfig(source_type=source_type),
        )
        await repository.create(source)
        await repository.transition(_WORKSPACE_ID, source.source_id, SourceStatus.INDEXING)
        sources[source_type] = await repository.transition(
            _WORKSPACE_ID, source.source_id, SourceStatus.READY
        )
    corpus = [
        _corpus_evidence(case, expected, sources[expected.source_type].source_id)
        for case in benchmark.cases
        for expected in case.expected_evidence
    ]
    retrievers = {
        mode: DeterministicCorpusRetriever(corpus, mode)
        for mode in (
            RetrievalMode.VECTOR,
            RetrievalMode.SENTENCE_WINDOW,
            RetrievalMode.LEXICAL,
            RetrievalMode.GRAPH,
            RetrievalMode.SQL,
        )
    }
    fusion = FusionRetriever(
        {
            mode: retriever
            for mode, retriever in retrievers.items()
            if mode is not RetrievalMode.SQL
        },
        max_top_k=20,
        candidate_multiplier=3,
        rrf_k=60,
        weights={},
        min_similarity=0.0,
        lexical_min_score=0.0,
    )
    return QueryService(
        repository,
        retrievers[RetrievalMode.VECTOR],
        retrievers[RetrievalMode.SENTENCE_WINDOW],
        ExtractiveGenerator(max_passages=20),
        default_top_k=5,
        max_top_k=20,
        min_similarity=0.0,
        graph_retriever=retrievers[RetrievalMode.GRAPH],
        lexical_retriever=retrievers[RetrievalMode.LEXICAL],
        fusion_retriever=fusion,
        database_retriever=retrievers[RetrievalMode.SQL],
        query_router=RuleBasedQueryRouter(confidence_threshold=0.70, winning_margin=0.15),
    )


def _query_request(
    case: GoldenCase, configuration: RetrievalConfiguration, workspace_id: str
) -> QueryRequest:
    values: dict[str, Any] = {
        "workspace_id": workspace_id,
        "question": case.question,
        "top_k": configuration.top_k,
        "retrieval_mode": configuration.retrieval_mode,
    }
    if configuration.retrieval_mode is RetrievalMode.FUSION:
        values.update(
            fusion_retrievers=configuration.retrievers,
            fusion_strategy=configuration.fusion_strategy or FusionStrategy.RRF,
            rerank=configuration.rerank,
        )
    return QueryRequest(**values)


def _corpus_evidence(case: GoldenCase, expected: ExpectedEvidence, source_id: UUID) -> Evidence:
    locator: dict[str, Any] = {}
    if expected.source_type is SourceType.PDF:
        locator["page_number"] = int(expected.locator.removeprefix("page "))
    elif expected.source_type is SourceType.WEBSITE:
        locator["source_uri"] = expected.locator
    elif expected.source_type is SourceType.CSV:
        locator["row_id"] = expected.locator.removeprefix("row ")
    else:
        table, _, detail = expected.locator.removeprefix("table ").partition(", ")
        locator["table_name"] = table
        if detail.startswith("record "):
            locator["row_id"] = detail.removeprefix("record ")
        else:
            locator["metadata"] = {"query_fingerprint": detail.removeprefix("query ")}
    document_id = str(uuid5(NAMESPACE_URL, f"evaluation-evidence:{expected.label}"))
    metadata = {
        **locator.pop("metadata", {}),
        "document_id": document_id,
        "parent_document_id": document_id,
        "graph_path": [{"relationship_id": document_id}],
        "evaluation_label": expected.label,
        "evaluation_query": case.question,
        "title": expected.label,
    }
    return Evidence(
        evidence_id=UUID(document_id),
        retriever="corpus",
        content=". ".join(expected.supporting_facts),
        source_id=source_id,
        source_type=expected.source_type,
        raw_score=1.0,
        normalized_score=1.0,
        metadata=metadata,
        **locator,
    )


def _normalize(value: str) -> str:
    return _NORMALIZE.sub(" ", value).strip().casefold()


def _error_category(error: Exception) -> str:
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", type(error).__name__).lower()
    return name[:128] or "evaluation_error"


def _evaluation_locator(citation: Citation) -> str:
    details = citation.locator_details
    if isinstance(details, DatabaseCitationLocator) and details.query_fingerprint:
        return f"table {details.table_name}, query {details.query_fingerprint}"
    return citation.locator

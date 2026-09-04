"""Cross-encoder scoring and deterministic diversity-aware re-ranking."""

import asyncio
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol, cast
from uuid import UUID

import numpy as np

from app.core.exceptions import RerankingError
from app.models import Evidence

logger = logging.getLogger(__name__)


class CrossEncoderModel(Protocol):
    """Subset of the sentence-transformers cross-encoder API used by the adapter."""

    def predict(
        self,
        sentences: list[tuple[str, str]],
        *,
        batch_size: int,
        show_progress_bar: bool,
    ) -> Any:
        """Score query-document pairs."""
        ...


ModelFactory = Callable[[str, int, str | None], CrossEncoderModel]


@dataclass(frozen=True, slots=True)
class RerankerScores:
    """Validated model outputs and inference latency."""

    scores: tuple[float, ...]
    model_name: str
    latency_seconds: float


class Reranker(Protocol):
    """Score an ordered evidence pool against one question."""

    @property
    def model_name(self) -> str:
        """Return the configured scoring model identity."""
        ...

    async def score(self, question: str, evidence: Sequence[Evidence]) -> RerankerScores:
        """Return one finite score per evidence item."""
        ...


class HuggingFaceCrossEncoderReranker:
    """Lazily load and execute a sentence-transformers cross-encoder."""

    def __init__(
        self,
        *,
        model_name: str,
        batch_size: int,
        max_length: int,
        device: str,
        model_factory: ModelFactory | None = None,
    ) -> None:
        self._model_name = model_name
        self._batch_size = batch_size
        self._max_length = max_length
        self._device = None if device == "auto" else device
        self._model_factory = model_factory or _create_cross_encoder
        self._model: CrossEncoderModel | None = None
        self._load_lock = asyncio.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    async def score(self, question: str, evidence: Sequence[Evidence]) -> RerankerScores:
        """Score query-evidence pairs outside the event loop."""
        if not evidence:
            return RerankerScores((), self.model_name, 0.0)
        model = await self._get_model()
        started = perf_counter()
        try:
            output = await asyncio.to_thread(
                model.predict,
                [(question, item.content) for item in evidence],
                batch_size=self._batch_size,
                show_progress_bar=False,
            )
            values = np.asarray(output, dtype=np.float64).reshape(-1)
        except Exception as error:
            raise RerankingError("Cross-encoder inference failed") from error
        latency = perf_counter() - started
        if len(values) != len(evidence):
            raise RerankingError("Cross-encoder returned an unexpected number of scores")
        if not np.isfinite(values).all():
            raise RerankingError("Cross-encoder returned a non-finite score")
        return RerankerScores(tuple(float(value) for value in values), self.model_name, latency)

    async def _get_model(self) -> CrossEncoderModel:
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is not None:
                return self._model
            try:
                self._model = await asyncio.to_thread(
                    self._model_factory,
                    self._model_name,
                    self._max_length,
                    self._device,
                )
            except Exception as error:
                raise RerankingError("Cross-encoder model loading failed") from error
            return self._model


class RerankingService:
    """Apply model ranking and a deterministic two-pass source-diversity policy."""

    def __init__(self, reranker: Reranker, *, max_per_source: int) -> None:
        if max_per_source < 1:
            raise ValueError("Re-ranking source cap must be positive")
        self._reranker = reranker
        self._max_per_source = max_per_source

    async def rerank(
        self, question: str, candidates: list[Evidence], *, top_k: int
    ) -> list[Evidence]:
        """Return relevance-ranked, citation-preserving, source-diverse evidence."""
        if top_k < 1:
            raise ValueError("Re-ranking top_k must be positive")
        if not candidates:
            return []
        total_started = perf_counter()
        result = await self._reranker.score(question, candidates)
        normalized = _normalize_scores(result.scores)
        ranked = sorted(
            zip(candidates, result.scores, normalized, range(1, len(candidates) + 1), strict=True),
            key=lambda item: (-item[1], item[3], str(item[0].evidence_id)),
        )
        selected: list[tuple[Evidence, float, float, int]] = []
        selected_ids: set[UUID] = set()
        source_counts: Counter[object] = Counter()
        for item in ranked:
            evidence = item[0]
            if source_counts[evidence.source_id] >= self._max_per_source:
                continue
            selected.append(item)
            selected_ids.add(evidence.evidence_id)
            source_counts[evidence.source_id] += 1
            if len(selected) == top_k:
                break
        if len(selected) < top_k:
            for item in ranked:
                if item[0].evidence_id in selected_ids:
                    continue
                selected.append(item)
                selected_ids.add(item[0].evidence_id)
                if len(selected) == top_k:
                    break
        total_latency = perf_counter() - total_started
        reranked: list[Evidence] = []
        for final_rank, (evidence, raw_score, normalized_score, fused_rank) in enumerate(
            selected, start=1
        ):
            reranked.append(
                evidence.model_copy(
                    update={
                        "retriever": "rerank",
                        "raw_score": raw_score,
                        "normalized_score": normalized_score,
                        "metadata": {
                            **evidence.metadata,
                            "reranking": {
                                "model_name": result.model_name,
                                "fused_rank": fused_rank,
                                "fused_score": evidence.raw_score,
                                "raw_score": raw_score,
                                "normalized_score": normalized_score,
                                "final_rank": final_rank,
                                "model_latency_ms": result.latency_seconds * 1000,
                                "total_latency_ms": total_latency * 1000,
                            },
                        },
                    },
                    deep=True,
                )
            )
        logger.info(
            "Evidence re-ranking complete",
            extra={
                "model_name": result.model_name,
                "candidate_count": len(candidates),
                "result_count": len(reranked),
                "reranking_latency_ms": total_latency * 1000,
            },
        )
        return reranked


def _normalize_scores(scores: tuple[float, ...]) -> tuple[float, ...]:
    if not scores:
        return ()
    minimum = min(scores)
    maximum = max(scores)
    if maximum == minimum:
        return tuple(1.0 for _ in scores)
    return tuple((score - minimum) / (maximum - minimum) for score in scores)


def _create_cross_encoder(
    model_name: str, max_length: int, device: str | None
) -> CrossEncoderModel:
    from sentence_transformers import CrossEncoder

    return cast(
        CrossEncoderModel,
        CrossEncoder(model_name, max_length=max_length, device=device),
    )

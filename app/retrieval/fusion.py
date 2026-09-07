"""Concurrent retrieval, canonical de-duplication, and rank fusion."""

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from app.core.exceptions import RetrievalError
from app.models import Evidence, FusionContribution, FusionStrategy, RetrievalMode
from app.retrieval.base import Retriever

DEFAULT_FUSION_RETRIEVERS = (
    RetrievalMode.VECTOR,
    RetrievalMode.SENTENCE_WINDOW,
    RetrievalMode.LEXICAL,
)


@dataclass(frozen=True, slots=True)
class _RankedCandidate:
    mode: RetrievalMode
    rank: int
    evidence: Evidence


class FusionRetriever:
    """Run configured retrievers and fuse their ranked candidates."""

    def __init__(
        self,
        retrievers: dict[RetrievalMode, Retriever],
        *,
        max_top_k: int,
        candidate_multiplier: int,
        rrf_k: int,
        weights: dict[RetrievalMode, float],
        min_similarity: float,
        lexical_min_score: float,
    ) -> None:
        if candidate_multiplier < 1 or rrf_k < 1:
            raise ValueError("Fusion candidate multiplier and RRF k must be positive")
        if any(weight < 0 for weight in weights.values()):
            raise ValueError("Fusion weights cannot be negative")
        self._retrievers = retrievers
        self._max_top_k = max_top_k
        self._candidate_multiplier = candidate_multiplier
        self._rrf_k = rrf_k
        self._weights = weights
        self._min_similarity = min_similarity
        self._lexical_min_score = lexical_min_score

    async def retrieve(
        self,
        workspace_id: str,
        query: str,
        *,
        top_k: int,
        source_ids: frozenset[UUID],
        modes: tuple[RetrievalMode, ...],
        strategy: FusionStrategy,
        min_similarity: float | None = None,
    ) -> list[Evidence]:
        """Return one deterministic fused list from all explicitly active retrievers."""
        if len(modes) < 2 or len(set(modes)) != len(modes):
            raise RetrievalError("Fused retrieval requires at least two distinct retrievers")
        if RetrievalMode.FUSION in modes:
            raise RetrievalError("Fusion cannot include itself as a retriever")
        missing = [mode.value for mode in modes if mode not in self._retrievers]
        if missing:
            raise RetrievalError("Retrieval is not configured for: " + ", ".join(missing))
        candidate_top_k = min(self._max_top_k, top_k * self._candidate_multiplier)
        results = await asyncio.gather(
            *(
                self._retrievers[mode].retrieve(
                    workspace_id,
                    query,
                    top_k=candidate_top_k,
                    source_ids=source_ids,
                    min_similarity=(
                        self._lexical_min_score
                        if mode is RetrievalMode.LEXICAL
                        else (self._min_similarity if min_similarity is None else min_similarity)
                    ),
                )
                for mode in modes
            )
        )
        return fuse_ranked_results(
            workspace_id,
            dict(zip(modes, results, strict=True)),
            top_k=top_k,
            strategy=strategy,
            rrf_k=self._rrf_k,
            weights=self._weights,
        )


def fuse_ranked_results(
    workspace_id: str,
    results: dict[RetrievalMode, list[Evidence]],
    *,
    top_k: int,
    strategy: FusionStrategy,
    rrf_k: int,
    weights: dict[RetrievalMode, float],
) -> list[Evidence]:
    """De-duplicate ranked evidence and compute RRF contributions."""
    if top_k < 1 or rrf_k < 1:
        raise ValueError("Fusion top_k and RRF k must be positive")
    effective_weights = _effective_weights(tuple(results), strategy, weights)
    grouped: dict[str, list[_RankedCandidate]] = defaultdict(list)
    for mode, evidence_items in results.items():
        for rank, evidence in enumerate(evidence_items, start=1):
            grouped[_canonical_key(workspace_id, mode, evidence)].append(
                _RankedCandidate(mode=mode, rank=rank, evidence=evidence)
            )
    fused: list[tuple[float, int, str, Evidence]] = []
    for canonical_key, candidates in grouped.items():
        best_by_mode: dict[RetrievalMode, _RankedCandidate] = {}
        for candidate in candidates:
            current = best_by_mode.get(candidate.mode)
            if current is None or candidate.rank < current.rank:
                best_by_mode[candidate.mode] = candidate
        unique_candidates = list(best_by_mode.values())
        contributions = [
            FusionContribution(
                retriever=candidate.mode,
                rank=candidate.rank,
                evidence_id=candidate.evidence.evidence_id,
                raw_score=candidate.evidence.raw_score,
                normalized_score=candidate.evidence.normalized_score,
                weight=effective_weights[candidate.mode],
                contribution=effective_weights[candidate.mode] / (rrf_k + candidate.rank),
            )
            for candidate in sorted(unique_candidates, key=lambda item: item.mode.value)
        ]
        score = sum(item.contribution for item in contributions)
        if score <= 0:
            continue
        representative = _representative(unique_candidates)
        metadata = {
            **representative.metadata,
            "canonical_evidence_key": canonical_key,
            "fusion_strategy": strategy.value,
            "fusion_contributions": [item.model_dump(mode="json") for item in contributions],
            "contributing_retrievers": [item.retriever.value for item in contributions],
        }
        evidence = representative.model_copy(
            update={
                "evidence_id": uuid5(NAMESPACE_URL, canonical_key),
                "retriever": "fusion",
                "raw_score": score,
                "metadata": metadata,
            },
            deep=True,
        )
        fused.append((score, min(item.rank for item in contributions), canonical_key, evidence))
    fused.sort(key=lambda item: (-item[0], item[1], item[2]))
    selected = fused[:top_k]
    maximum = selected[0][0] if selected else 0.0
    return [
        item[3].model_copy(update={"normalized_score": item[0] / maximum}, deep=True)
        for item in selected
    ]


def _effective_weights(
    modes: tuple[RetrievalMode, ...],
    strategy: FusionStrategy,
    weights: dict[RetrievalMode, float],
) -> dict[RetrievalMode, float]:
    if strategy is FusionStrategy.RRF:
        return dict.fromkeys(modes, 1.0)
    active = {mode: weights.get(mode, 0.0) for mode in modes}
    total = sum(active.values())
    if total <= 0:
        raise RetrievalError("Active fusion retrievers must have a positive total weight")
    return {mode: weight / total for mode, weight in active.items()}


def _canonical_key(workspace_id: str, mode: RetrievalMode, evidence: Evidence) -> str:
    if mode in {RetrievalMode.VECTOR, RetrievalMode.LEXICAL}:
        identity = evidence.metadata.get("document_id")
        kind = "document"
    elif mode is RetrievalMode.SENTENCE_WINDOW:
        identity = evidence.metadata.get("parent_document_id")
        kind = "document"
    elif mode is RetrievalMode.GRAPH:
        path = evidence.metadata.get("graph_path")
        if not isinstance(path, list):
            raise RetrievalError("Graph evidence is missing path identity metadata")
        relationship_ids = [item.get("relationship_id") for item in path if isinstance(item, dict)]
        identity = ">".join(item for item in relationship_ids if isinstance(item, str))
        kind = "graph"
    else:
        raise RetrievalError(f"Unsupported fusion retriever: {mode.value}")
    if not isinstance(identity, str) or not identity:
        raise RetrievalError(f"{mode.value} evidence is missing canonical identity metadata")
    return f"{workspace_id}:{kind}:{identity}"


def _representative(candidates: list[_RankedCandidate]) -> Evidence:
    return min(
        candidates,
        key=lambda item: (
            0 if item.mode is RetrievalMode.SENTENCE_WINDOW else 1,
            item.rank,
            str(item.evidence.evidence_id),
        ),
    ).evidence

"""Deterministic intent scoring and safe retrieval-plan selection."""

import re
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.core.exceptions import RoutingError
from app.models import (
    RetrievalMode,
    RoutingIntent,
    RoutingKind,
    RoutingRuleMatch,
    RoutingTrace,
    Source,
    SourceStatus,
    SourceType,
)
from app.retrieval.fusion import DEFAULT_FUSION_RETRIEVERS

_WHITESPACE = re.compile(r"\s+")
_IDENTIFIER = re.compile(r"\b[A-Za-z]{2,}[\-_]\d{2,}[A-Za-z0-9\-_]*\b")
_ACRONYM = re.compile(r"\b[A-Z]{2,6}\b")


@dataclass(frozen=True, slots=True)
class _Rule:
    rule_id: str
    intent: RoutingIntent
    pattern: re.Pattern[str]
    score: float


_RULES = (
    _Rule(
        "sql_aggregate",
        RoutingIntent.SQL,
        re.compile(r"\b(total|sum|average|avg|count|how many|maximum|minimum|highest|lowest)\b"),
        0.70,
    ),
    _Rule(
        "sql_grouping",
        RoutingIntent.SQL,
        re.compile(r"\b(compare|group(?:ed)?|breakdown|trend)\b|\bby\s+\w+"),
        0.60,
    ),
    _Rule(
        "sql_filter",
        RoutingIntent.SQL,
        re.compile(r"\b(list|open|older than|newer than|before|after|between|where)\b"),
        0.65,
    ),
    _Rule(
        "sql_metric",
        RoutingIntent.SQL,
        re.compile(
            r"\b(revenue|sales|churn|tickets?|orders?|customers?|employees?|amount|price|cost)\b"
        ),
        0.15,
    ),
    _Rule(
        "graph_relationship",
        RoutingIntent.GRAPH,
        re.compile(
            r"\b(owns?|manages?|reports? to|responsible for|depends? on|connected to|"
            r"governs?|relationship between)\b"
        ),
        0.85,
    ),
    _Rule(
        "graph_subject",
        RoutingIntent.GRAPH,
        re.compile(r"\b(who|which person|which team|which department)\b"),
        0.10,
    ),
    _Rule(
        "window_context",
        RoutingIntent.SENTENCE_WINDOW,
        re.compile(
            r"\b(surrounding (?:text|context|passage)|paragraph around|context around|"
            r"before and after|nearby paragraph|explain the paragraph)\b"
        ),
        0.90,
    ),
    _Rule(
        "lexical_exact",
        RoutingIntent.LEXICAL,
        re.compile(r"\b(exact|identifier|reference|policy number|ticket number|code)\b"),
        0.55,
    ),
)

_PLANS: dict[RoutingIntent, tuple[RetrievalMode, ...]] = {
    RoutingIntent.SQL: (RetrievalMode.SQL,),
    RoutingIntent.GRAPH: (RetrievalMode.GRAPH, RetrievalMode.VECTOR),
    RoutingIntent.SENTENCE_WINDOW: (
        RetrievalMode.SENTENCE_WINDOW,
        RetrievalMode.VECTOR,
    ),
    RoutingIntent.LEXICAL: (RetrievalMode.LEXICAL, RetrievalMode.VECTOR),
    RoutingIntent.DEFAULT: DEFAULT_FUSION_RETRIEVERS,
}


def normalize_query(question: str) -> str:
    """Normalize safe surface variation without changing query intent."""
    return _WHITESPACE.sub(" ", question.strip())


class RuleBasedQueryRouter:
    """Select the smallest reliable retrieval plan from deterministic signals."""

    def __init__(self, *, confidence_threshold: float, winning_margin: float) -> None:
        if not 0 <= confidence_threshold <= 1 or not 0 <= winning_margin <= 1:
            raise ValueError("Routing confidence and margin must be between zero and one")
        self._confidence_threshold = confidence_threshold
        self._winning_margin = winning_margin

    def route(
        self,
        question: str,
        sources: Sequence[Source],
        requested_source_ids: Sequence[UUID],
        available_retrievers: frozenset[RetrievalMode],
    ) -> RoutingTrace:
        """Return an inspectable, source-aware automatic retrieval plan."""
        started = time.perf_counter()
        matches = self._match_rules(normalize_query(question))
        scores: defaultdict[RoutingIntent, float] = defaultdict(float)
        for match in matches:
            scores[match.intent] = min(1.0, scores[match.intent] + match.score)
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0].value))
        winner, confidence = ranked[0] if ranked else (RoutingIntent.DEFAULT, 0.0)
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        fallback_reason: str | None = None
        if confidence < self._confidence_threshold:
            winner = RoutingIntent.DEFAULT
            fallback_reason = "confidence_below_threshold"
        elif confidence - runner_up < self._winning_margin:
            winner = RoutingIntent.DEFAULT
            fallback_reason = "mixed_or_ambiguous_intent"

        selected_sources = list(requested_source_ids)
        if winner is RoutingIntent.SQL:
            selected_sources = self._select_database(sources, requested_source_ids)
            if not selected_sources:
                winner = RoutingIntent.DEFAULT
                fallback_reason = "no_ready_database_source"
        plan = _PLANS[winner]
        if not set(plan).issubset(available_retrievers):
            unavailable = ",".join(
                sorted(mode.value for mode in plan if mode not in available_retrievers)
            )
            winner = RoutingIntent.DEFAULT
            plan = _PLANS[winner]
            fallback_reason = f"retriever_unavailable:{unavailable}"
            selected_sources = list(requested_source_ids)
        return RoutingTrace(
            kind=RoutingKind.AUTO,
            intent=winner,
            confidence=confidence,
            selected_retrievers=list(plan),
            selected_source_ids=selected_sources,
            matched_rules=matches,
            fallback_reason=fallback_reason,
            routing_latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _match_rules(self, question: str) -> list[RoutingRuleMatch]:
        matches: list[RoutingRuleMatch] = []
        lowered = question.lower()
        for rule in _RULES:
            match = rule.pattern.search(lowered)
            if match:
                matches.append(
                    RoutingRuleMatch(
                        rule_id=rule.rule_id,
                        intent=rule.intent,
                        score=rule.score,
                        matched_text=match.group(0),
                    )
                )
        identifier = _IDENTIFIER.search(question)
        if identifier:
            matches.append(
                RoutingRuleMatch(
                    rule_id="lexical_identifier",
                    intent=RoutingIntent.LEXICAL,
                    score=0.85,
                    matched_text=identifier.group(0),
                )
            )
        elif acronym := _ACRONYM.search(question):
            matches.append(
                RoutingRuleMatch(
                    rule_id="lexical_acronym",
                    intent=RoutingIntent.LEXICAL,
                    score=0.75,
                    matched_text=acronym.group(0),
                )
            )
        return matches

    def _select_database(
        self, sources: Sequence[Source], requested_source_ids: Sequence[UUID]
    ) -> list[UUID]:
        requested = set(requested_source_ids)
        databases = [
            source
            for source in sources
            if source.status is SourceStatus.READY
            and source.config.source_type is SourceType.DATABASE
            and (not requested or source.source_id in requested)
        ]
        if len(databases) > 1:
            raise RoutingError("Automatic SQL routing requires selecting one database source")
        if not databases:
            return []
        return [databases[0].source_id]

"""Reusable native Streamlit renderers for grounded query diagnostics."""

from collections.abc import Iterable

import streamlit as st

from app.models import Citation, QueryResponse, RoutingTrace


def render_citations(response: QueryResponse, *, expanded: bool = False) -> None:
    """Render claim-linked citation inspectors."""
    if not response.citations:
        st.caption("No citations returned.")
        return
    st.caption("Sources")
    segments = response.citation_segments or []
    for citation in response.citations:
        _render_citation(citation, segments, expanded=expanded)


def _render_citation(
    citation: Citation, segments: Iterable[object], *, expanded: bool = False
) -> None:
    source_label = citation.source_title or citation.source_type.value
    with st.expander(
        f"[{citation.citation_id}] {source_label} · {citation.locator}", expanded=expanded
    ):
        for segment in segments:
            if citation.citation_id in segment.citation_ids:  # type: ignore[attr-defined]
                st.write(f"Supports: {segment.text}")  # type: ignore[attr-defined]
        st.write(citation.excerpt)
        st.caption(f"Retriever: {citation.retriever} · Score: {citation.score:.3f}")
        details = citation.locator_details
        if details.source_type.value == "website":
            st.link_button("Open source page", str(details.url))  # type: ignore[union-attr]
        fingerprint = getattr(details, "query_fingerprint", None)
        if fingerprint:
            st.caption(f"Query fingerprint: {fingerprint[:12]}")


def render_routing_trace(trace: RoutingTrace | None, *, expanded: bool = False) -> None:
    """Render routing choices as secondary diagnostics."""
    if trace is None:
        return
    with st.expander("How this question was routed", expanded=expanded):
        st.write(f"**Intent:** {trace.intent.value}")
        st.write(f"**Retrievers:** {', '.join(mode.value for mode in trace.selected_retrievers)}")
        st.write(f"**Confidence:** {trace.confidence:.0%}")
        if trace.fallback_reason:
            st.caption(f"Fallback: {trace.fallback_reason}")
        st.caption(f"Routing time: {trace.routing_latency_ms:.2f} ms")


def render_query_response(
    response: QueryResponse, *, show_evidence: bool = False, expand_diagnostics: bool = False
) -> None:
    """Render a grounded response and optional ranked evidence."""
    if response.insufficient_evidence:
        st.info(response.answer)
    else:
        st.write(response.answer)
    render_citations(response, expanded=expand_diagnostics)
    if show_evidence:
        with st.expander("Ranked evidence", expanded=expand_diagnostics):
            for rank, evidence in enumerate(response.evidence, start=1):
                st.write(f"**{rank}. {evidence.source_type.value} · {evidence.retriever}**")
                st.write(evidence.content)
                score = evidence.normalized_score
                score_label = "n/a" if score is None else f"{score:.3f}"
                st.caption(f"Score: {score_label} · Source: {evidence.source_id}")
    render_routing_trace(response.routing_trace, expanded=expand_diagnostics)

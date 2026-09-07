"""Phase 15 Streamlit page functions."""

import hashlib
from typing import Any, cast

import streamlit as st

from app.models import (
    DatabaseSourceRequest,
    QueryRequest,
    RetrievalMode,
    Source,
    SourceStatus,
    SourceType,
    WebsiteIngestionRequest,
)
from app.models.system import EffectiveSettings, EvaluationReportListing
from ui.api_client import ApiError, RagApiClient
from ui.renderers import render_query_response

WORKSPACE_ID = "local"


def _client() -> RagApiClient:
    return cast(RagApiClient, st.session_state["rag_api_client"])


@st.cache_data(ttl=5, show_spinner=False)  # type: ignore[untyped-decorator]
def _cached_sources(base_url: str, workspace_id: str) -> list[Source]:
    return RagApiClient(base_url).list_sources(workspace_id)


@st.cache_data(ttl=10, show_spinner=False)  # type: ignore[untyped-decorator]
def _cached_settings(base_url: str) -> EffectiveSettings:
    return RagApiClient(base_url).settings()


@st.cache_data(ttl=5, show_spinner=False)  # type: ignore[untyped-decorator]
def _cached_reports(base_url: str) -> EvaluationReportListing:
    return RagApiClient(base_url).list_reports()


def _invalidate_sources() -> None:
    _cached_sources.clear()


def _page_size() -> int:
    return int(st.session_state.get("table_page_size", 25))


def _expand_diagnostics() -> bool:
    return bool(st.session_state.get("expand_diagnostics", False))


def can_inspect_documents(source: Source) -> bool:
    """Return whether a source owns normalized documents in local source storage."""
    return source.config.source_type is not SourceType.DATABASE


def valid_sql_selection(sources: list[Source]) -> bool:
    """Require exactly one database source for structured retrieval."""
    return len(sources) == 1 and sources[0].config.source_type is SourceType.DATABASE


def quality_chart_metrics(metrics: dict[str, Any]) -> dict[str, float]:
    """Select quality ratios while excluding latency and cost units."""
    names = (
        "recall_at_k",
        "precision_at_k",
        "hit_rate_at_k",
        "mean_reciprocal_rank",
        "ndcg_at_k",
        "citation_accuracy",
        "faithfulness",
        "answer_relevance",
        "route_accuracy",
        "sql_safety_accuracy",
        "llm_faithfulness",
    )
    return {name: float(metrics[name]) for name in names if metrics.get(name) is not None}


def _sources(ready_only: bool = False) -> list[Source]:
    sources = _cached_sources(_client().base_url, WORKSPACE_ID)
    return (
        [item for item in sources if item.status is SourceStatus.READY] if ready_only else sources
    )


def _options(sources: list[Source]) -> dict[str, Source]:
    return {
        f"{item.name} · {item.config.source_type.value} · {str(item.source_id)[:8]}": item
        for item in sources
    }


def chat_page() -> None:
    st.title("Chat")
    st.caption("Ask grounded questions across your ready knowledge sources.")
    try:
        options = _options(_sources(True))
    except ApiError as error:
        st.error(str(error))
        return
    if not options:
        st.info("Add a ready PDF, CSV, website, or database source to begin.")
        st.chat_input("No ready sources", disabled=True)
        return
    selected = st.multiselect("Sources", options, default=list(options), key="chat_sources")
    for turn in st.session_state.setdefault("chat_history", []):
        with st.chat_message("user"):
            st.write(turn["question"])
        with st.chat_message("assistant"):
            render_query_response(turn["response"], expand_diagnostics=_expand_diagnostics())
    if question := st.chat_input("Ask across selected sources", disabled=not selected):
        with st.chat_message("user"):
            st.write(question)
        try:
            response = _client().query(
                QueryRequest(
                    workspace_id=WORKSPACE_ID,
                    question=question,
                    source_ids=[options[label].source_id for label in selected],
                    retrieval_mode=RetrievalMode.AUTO,
                )
            )
            with st.chat_message("assistant"):
                render_query_response(response, expand_diagnostics=_expand_diagnostics())
            st.session_state["chat_history"].append({"question": question, "response": response})
        except ApiError as error:
            st.error(str(error))


def sources_page() -> None:
    st.title("Sources")
    st.caption(f"Register and inspect knowledge sources in workspace `{WORKSPACE_ID}`.")
    inventory, add = st.tabs(["Inventory", "Add source"])
    with inventory:
        _inventory()
    with add:
        kind = st.radio("Source type", ["PDF", "Website", "CSV", "PostgreSQL"], horizontal=True)
        {
            "PDF": _pdf_form,
            "Website": _website_form,
            "CSV": _csv_form,
            "PostgreSQL": _database_form,
        }[kind]()


def _inventory() -> None:
    try:
        sources = _sources()
    except ApiError as error:
        st.error(str(error))
        return
    if not sources:
        st.info("No sources are registered yet.")
        return
    rows = [
        {
            "Name": s.name,
            "Type": s.config.source_type.value,
            "Status": s.status.value,
            "Updated": s.updated_at.isoformat(),
            "Source ID": str(s.source_id),
        }
        for s in sources
    ]
    st.dataframe(
        rows[: _page_size()],
        use_container_width=True,
        hide_index=True,
    )
    if len(rows) > _page_size():
        st.caption(f"Showing {_page_size()} of {len(rows)} sources.")
    options = _options(sources)
    source = options[st.selectbox("Inspect source", options)]
    left, right = st.columns(2)
    if left.button(
        "Load documents", use_container_width=True, disabled=not can_inspect_documents(source)
    ):
        try:
            st.session_state["source_documents"] = _client().list_documents(
                WORKSPACE_ID, source.source_id
            )
            st.session_state["source_documents_id"] = source.source_id
        except ApiError as error:
            st.error(str(error))
    if right.button(
        "Rebuild retrieval index",
        type="primary",
        use_container_width=True,
        disabled=source.config.source_type.value == "database",
    ):
        try:
            result = _client().rebuild_index(WORKSPACE_ID, source.source_id)
            _invalidate_sources()
            st.success(
                f"Indexed {result.document_count} document(s). Generation {result.generation_id}"
            )
        except ApiError as error:
            st.error(str(error))
    if st.session_state.get("source_documents_id") == source.source_id:
        documents = st.session_state.get("source_documents", [])
        visible_documents = documents[: _page_size()]
        st.caption(f"Showing {len(visible_documents)} of {len(documents)} normalized document(s)")
        for number, document in enumerate(visible_documents, 1):
            locator = document.metadata.get("locator", document.metadata.get("page", number))
            with st.expander(f"Document {number} · {locator}"):
                st.write(document.content)
                st.json(document.metadata)


def _pdf_form() -> None:
    upload = st.file_uploader("PDF file", type=["pdf"], key="source_pdf")
    if st.button("Add PDF", type="primary", use_container_width=True, disabled=upload is None):
        assert upload is not None
        try:
            result = _client().add_pdf(WORKSPACE_ID, upload.name, upload.getvalue())
            _invalidate_sources()
            st.success(f"Added {result.source.name} with {result.chunk_count} chunk(s)")
        except ApiError as error:
            st.error(str(error))


def _website_form() -> None:
    url = st.text_input("Website URL", placeholder="https://docs.example.com/")
    crawl = st.checkbox("Crawl same-domain links")
    limit = st.number_input("Page limit", 1, 100, 20, disabled=not crawl)
    if st.button("Add website", type="primary", use_container_width=True, disabled=not url):
        try:
            result = _client().add_website(
                WebsiteIngestionRequest(
                    workspace_id=WORKSPACE_ID,
                    url=url,
                    crawl_same_domain=crawl,
                    page_limit=int(limit) if crawl else 1,
                )
            )
            _invalidate_sources()
            st.success(f"Added {result.source.name} with {result.page_count} page(s)")
        except ApiError as error:
            st.error(str(error))


def _csv_form() -> None:
    upload = st.file_uploader("CSV file", type=["csv"], key="source_csv")
    if upload is None:
        return
    data = upload.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    if st.session_state.get("csv_digest") != digest:
        st.session_state.pop("csv_preview", None)
    if st.button("Preview CSV", use_container_width=True):
        try:
            st.session_state["csv_preview"] = _client().preview_csv(upload.name, data)
            st.session_state["csv_digest"] = digest
        except ApiError as error:
            st.error(str(error))
    preview = st.session_state.get("csv_preview")
    if preview is None or st.session_state.get("csv_digest") != digest:
        return
    st.caption(f"{preview.row_count} data row(s)")
    st.dataframe(preview.sample_rows, use_container_width=True, hide_index=True)
    columns = [column.name for column in preview.columns]
    text_columns = st.multiselect("Searchable text columns", columns)
    metadata_columns = st.multiselect("Metadata columns", columns)
    row_id = st.selectbox("Row identifier", ["Use row number", *columns])
    if st.button("Add CSV", type="primary", use_container_width=True, disabled=not text_columns):
        form: dict[str, Any] = {
            "workspace_id": WORKSPACE_ID,
            "text_columns": text_columns,
            "metadata_columns": metadata_columns,
        }
        if row_id != "Use row number":
            form["row_id_column"] = row_id
        try:
            result = _client().add_csv(upload.name, data, form)
            _invalidate_sources()
            st.success(f"Added {result.source.name} with {result.document_count} document(s)")
        except ApiError as error:
            st.error(str(error))


def _database_form() -> None:
    with st.expander("Connect PostgreSQL", expanded=True):
        name = st.text_input("Source name")
        secret = st.text_input("Connection secret environment variable")
        shared = (
            st.radio("Tenant isolation", ["Dedicated database/schema", "Shared tables"])
            == "Shared tables"
        )
        specs = st.text_area(
            "Allowed tables", placeholder="public.sales:workspace_id" if shared else "public.sales"
        )
        if st.button(
            "Connect database",
            type="primary",
            use_container_width=True,
            disabled=not (name and secret and specs),
        ):
            try:
                result = _client().add_database(
                    DatabaseSourceRequest.model_validate(
                        {
                            "workspace_id": WORKSPACE_ID,
                            "name": name,
                            "secret_env_var": secret,
                            "dialect": "postgresql",
                            "isolation_mode": "shared" if shared else "dedicated",
                            "tables": parse_table_specs(specs, shared=shared),
                        }
                    )
                )
                _invalidate_sources()
                st.success(f"Connected {result.source.name} with {len(result.tables)} table(s)")
            except (ApiError, ValueError) as error:
                st.error(str(error))


def parse_table_specs(specs: str, *, shared: bool) -> list[dict[str, str]]:
    tables: list[dict[str, str]] = []
    for raw in specs.splitlines():
        value = raw.strip()
        if not value:
            continue
        table_part, separator, tenant = value.partition(":")
        schema, dot, table_name = table_part.partition(".")
        if not dot:
            table_name, schema = schema, ""
        if not table_name.strip() or (shared and (not separator or not tenant.strip())):
            raise ValueError("Shared table entries must include :tenant_column.")
        item = {"table_name": table_name.strip()}
        if schema.strip():
            item["schema_name"] = schema.strip()
        if shared:
            item["tenant_column"] = tenant.strip()
        tables.append(item)
    if not tables:
        raise ValueError("Add at least one allowed table.")
    return tables


def retrieval_lab_page() -> None:
    st.title("Retrieval Lab")
    st.caption("Test retrieval choices without changing backend defaults.")
    try:
        options = _options(_sources(True))
        settings = _cached_settings(_client().base_url)
    except ApiError as error:
        st.error(str(error))
        return
    selected = st.multiselect("Sources", options, default=list(options), key="lab_sources")
    modes = [
        RetrievalMode.AUTO,
        RetrievalMode.VECTOR,
        RetrievalMode.SENTENCE_WINDOW,
        RetrievalMode.LEXICAL,
        RetrievalMode.FUSION,
    ]
    if settings.graph_retrieval_configured:
        modes.append(RetrievalMode.GRAPH)
    if settings.sql_retrieval_configured:
        modes.append(RetrievalMode.SQL)
    mode = RetrievalMode(st.selectbox("Retrieval mode", [item.value for item in modes]))
    top_k = st.slider("Top K", 1, settings.retrieval_max_top_k, settings.retrieval_top_k)
    threshold = st.slider("Minimum similarity", -1.0, 1.0, settings.retrieval_min_similarity, 0.05)
    retrievers: list[RetrievalMode] | None = None
    strategy: str | None = None
    rerank = False
    if mode is RetrievalMode.FUSION:
        choices = ["vector", "sentence_window", "lexical"]
        if settings.graph_retrieval_configured:
            choices.append("graph")
        retrievers = [
            RetrievalMode(value)
            for value in st.multiselect("Fusion retrievers", choices, default=choices[:3])
        ]
        strategy = st.selectbox("Fusion strategy", ["rrf", "weighted_rrf"])
        rerank = st.checkbox("Re-rank fused candidates")
    question = st.text_area("Question", max_chars=4000)
    selected_sources = [options[label] for label in selected]
    invalid_sql = mode is RetrievalMode.SQL and not valid_sql_selection(selected_sources)
    invalid = invalid_sql or (mode is RetrievalMode.FUSION and len(retrievers or []) < 2)
    if invalid_sql:
        st.info("SQL retrieval requires exactly one database source.")
    if st.button(
        "Run experiment",
        type="primary",
        use_container_width=True,
        disabled=not question or not selected or invalid,
    ):
        payload: dict[str, Any] = {
            "workspace_id": WORKSPACE_ID,
            "question": question,
            "top_k": top_k,
            "min_similarity": threshold,
            "source_ids": [options[label].source_id for label in selected],
            "retrieval_mode": mode,
            "rerank": rerank,
        }
        if mode is RetrievalMode.FUSION:
            payload.update(fusion_retrievers=retrievers, fusion_strategy=strategy)
        try:
            st.session_state["lab_result"] = _client().query_timed(
                QueryRequest.model_validate(payload)
            )
        except (ApiError, ValueError) as error:
            st.error(str(error))
    if result := st.session_state.get("lab_result"):
        st.caption(f"Total API time: {result.elapsed_seconds:.3f} seconds")
        render_query_response(
            result.response, show_evidence=True, expand_diagnostics=_expand_diagnostics()
        )


def evaluation_page() -> None:
    st.title("Evaluation")
    st.caption("Inspect benchmark results produced by the Phase 14 evaluation runner.")
    try:
        listing = _cached_reports(_client().base_url)
    except ApiError as error:
        st.error(str(error))
        return
    if listing.invalid_report_ids:
        st.warning(
            "Ignored invalid evaluation artifact(s): " + ", ".join(listing.invalid_report_ids)
        )
    if not listing.reports:
        st.info("No evaluation reports are available in the configured report directory.")
        return
    labels = {
        f"{report.configuration_name} · {report.tier} · {report.created_at.isoformat()}": report
        for report in listing.reports
    }
    try:
        report = _client().get_report(labels[st.selectbox("Report", labels)].report_id)
    except ApiError as error:
        st.error(str(error))
        return
    status = "Passed" if report.passed else "Not gated" if report.passed is None else "Failed"
    st.subheader(f"{report.configuration.name} · {status}")
    report_identity = f"Benchmark {report.benchmark_version} · {report.tier.value}"
    st.caption(f"{report_identity} · {report.created_at.isoformat()}")
    metrics = report.aggregate.model_dump()
    columns = st.columns(4)
    columns[0].metric("Recall@K", f"{metrics['recall_at_k']:.1%}")
    columns[1].metric("Citation accuracy", f"{metrics['citation_accuracy']:.1%}")
    columns[2].metric("Faithfulness", f"{metrics['faithfulness']:.1%}")
    columns[3].metric("P95 latency", f"{metrics['p95_latency_seconds']:.2f}s")
    st.bar_chart(quality_chart_metrics(metrics))
    if report.gates:
        st.subheader("Deployment gates")
        st.dataframe(
            [gate.model_dump() for gate in report.gates], use_container_width=True, hide_index=True
        )
    if report.slices:
        with st.expander("Metric slices"):
            name = st.selectbox("Slice", sorted(report.slices))
            st.json(report.slices[name].model_dump(mode="json"))
    with st.expander("Retrieval configuration"):
        st.json(report.configuration.model_dump(mode="json"))
    with st.expander("Case diagnostics"):
        st.dataframe(
            [
                {
                    "case_id": item.case_id,
                    "latency_seconds": item.latency_seconds,
                    "retrieved": len(item.retrieved_evidence_labels),
                    "cited": len(item.cited_evidence_labels),
                    "error": item.error_category,
                }
                for item in report.observations[: _page_size()]
            ],
            use_container_width=True,
            hide_index=True,
        )


def settings_page() -> None:
    st.title("Settings")
    st.caption("Backend configuration is read-only; interface preferences last for this session.")
    try:
        settings = _cached_settings(_client().base_url)
        st.success("API connected")
        st.subheader("Effective backend configuration")
        st.json(settings.model_dump(mode="json"))
    except ApiError as error:
        st.error(str(error))
    st.subheader("Interface preferences")
    st.checkbox("Expand diagnostics by default", key="expand_diagnostics")
    st.number_input("Preferred table page size", 10, 100, key="table_page_size")
    st.caption(f"Workspace is fixed to `{WORKSPACE_ID}` for the MVP.")

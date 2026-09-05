"""Streamlit source-ingestion shell for Enterprise Knowledge Fusion RAG."""

import hashlib
from typing import Any

import httpx
import streamlit as st

from app.core.config import get_settings

st.set_page_config(page_title="Enterprise Knowledge Fusion RAG", page_icon="🔎", layout="wide")

settings = get_settings()
workspace_id = "local"

st.title("Enterprise Knowledge Fusion RAG")
st.caption("Grounded answers across documents, websites, and structured data.")

with st.sidebar:
    st.header("Knowledge sources")
    st.caption(f"Workspace: `{workspace_id}`")
    uploaded_pdf = st.file_uploader(
        "Upload PDF",
        type=["pdf"],
        help="PDF only, up to 25 MB. Scanned PDFs require OCR and are not supported yet.",
    )
    if uploaded_pdf is not None and st.button("Add PDF", type="primary", use_container_width=True):
        with st.status("Ingesting PDF…", expanded=True) as ingestion_status:
            try:
                response = httpx.post(
                    f"{settings.api_base_url}/sources/pdf",
                    data={"workspace_id": workspace_id},
                    files={"file": (uploaded_pdf.name, uploaded_pdf.getvalue(), "application/pdf")},
                    timeout=60,
                )
                response.raise_for_status()
                result: dict[str, Any] = response.json()
                ingestion_status.write(f"Parsed {result['page_count']} page(s)")
                ingestion_status.write(f"Created {result['chunk_count']} chunk(s)")
                ingestion_status.update(label="PDF ready", state="complete", expanded=False)
                st.success(f"Added {result['source']['name']}")
            except httpx.HTTPStatusError as error:
                detail = error.response.json().get("detail", "The API rejected the PDF")
                ingestion_status.update(label="PDF ingestion failed", state="error")
                st.error(str(detail))
            except httpx.RequestError:
                ingestion_status.update(label="API unavailable", state="error")
                st.error("Could not reach the ingestion API. Retry after the API is running.")

    st.divider()
    uploaded_csv = st.file_uploader(
        "Upload CSV",
        type=["csv"],
        help="Comma-delimited UTF-8 CSV, up to 25 MB, with a required header row.",
        key="rag_csv_upload",
    )
    if uploaded_csv is not None:
        csv_data = uploaded_csv.getvalue()
        csv_digest = hashlib.sha256(csv_data).hexdigest()
        if st.session_state.get("rag_csv_preview_digest") != csv_digest:
            st.session_state.pop("rag_csv_preview", None)
        if st.button("Preview CSV", use_container_width=True):
            try:
                response = httpx.post(
                    f"{settings.api_base_url}/sources/csv/preview",
                    files={"file": (uploaded_csv.name, csv_data, "text/csv")},
                    timeout=60,
                )
                response.raise_for_status()
                st.session_state["rag_csv_preview"] = response.json()
                st.session_state["rag_csv_preview_digest"] = csv_digest
            except httpx.HTTPStatusError as error:
                detail = error.response.json().get("detail", "The API rejected the CSV")
                st.error(str(detail))
            except httpx.RequestError:
                st.error("Could not reach the ingestion API. Retry after the API is running.")

        csv_preview: dict[str, Any] | None = st.session_state.get("rag_csv_preview")
        if csv_preview is not None and st.session_state.get("rag_csv_preview_digest") == csv_digest:
            st.caption(f"{csv_preview['row_count']} data row(s)")
            schema_rows = [
                {"Column": column["name"], "Inferred type": column["inferred_type"]}
                for column in csv_preview["columns"]
            ]
            st.dataframe(schema_rows, use_container_width=True, hide_index=True)
            with st.expander("Sample rows"):
                st.dataframe(csv_preview["sample_rows"], use_container_width=True, hide_index=True)
            csv_columns = [column["name"] for column in csv_preview["columns"]]
            selected_text_columns = st.multiselect(
                "Searchable text columns",
                csv_columns,
                help="At least one column is required. Labels are preserved in row content.",
            )
            selected_metadata_columns = st.multiselect(
                "Metadata columns",
                csv_columns,
                help="Optional structured values retained for inspection and future filtering.",
            )
            row_id_options = ["Use row number", *csv_columns]
            selected_row_id = st.selectbox(
                "Row identifier",
                row_id_options,
                help="A selected column must be unique and non-empty.",
            )
            if st.button(
                "Add CSV",
                type="primary",
                use_container_width=True,
                disabled=not selected_text_columns,
            ):
                with st.status("Ingesting CSV…", expanded=True) as ingestion_status:
                    try:
                        form_data: dict[str, Any] = {
                            "workspace_id": workspace_id,
                            "text_columns": selected_text_columns,
                            "metadata_columns": selected_metadata_columns,
                        }
                        if selected_row_id != "Use row number":
                            form_data["row_id_column"] = selected_row_id
                        response = httpx.post(
                            f"{settings.api_base_url}/sources/csv",
                            data=form_data,
                            files={"file": (uploaded_csv.name, csv_data, "text/csv")},
                            timeout=120,
                        )
                        response.raise_for_status()
                        result = response.json()
                        ingestion_status.write(f"Read {result['row_count']} row(s)")
                        ingestion_status.write(
                            f"Created {result['document_count']} row document(s)"
                        )
                        if result["skipped_count"]:
                            ingestion_status.write(
                                f"Skipped {result['skipped_count']} empty row(s)"
                            )
                        ingestion_status.update(label="CSV ready", state="complete", expanded=False)
                        st.success(f"Added {result['source']['name']}")
                    except httpx.HTTPStatusError as error:
                        detail = error.response.json().get("detail", "The API rejected the CSV")
                        ingestion_status.update(label="CSV ingestion failed", state="error")
                        st.error(str(detail))
                    except httpx.RequestError:
                        ingestion_status.update(label="API unavailable", state="error")
                        st.error(
                            "Could not reach the ingestion API. Retry after the API is running."
                        )

    st.divider()
    website_url = st.text_input(
        "Website URL",
        placeholder="https://docs.example.com/",
        help="Public HTTP(S) pages only. JavaScript-rendered content is not supported yet.",
    )
    crawl_same_domain = st.checkbox(
        "Crawl same-domain links",
        help="Follows links on the exact same hostname while respecting robots.txt.",
    )
    page_limit = st.number_input(
        "Page limit",
        min_value=1,
        max_value=settings.website_max_pages,
        value=min(20, settings.website_max_pages),
        disabled=not crawl_same_domain,
    )
    if website_url and st.button("Add website", type="primary", use_container_width=True):
        with st.status("Crawling website…", expanded=True) as ingestion_status:
            try:
                response = httpx.post(
                    f"{settings.api_base_url}/sources/website",
                    json={
                        "workspace_id": workspace_id,
                        "url": website_url,
                        "crawl_same_domain": crawl_same_domain,
                        "page_limit": int(page_limit) if crawl_same_domain else 1,
                    },
                    timeout=120,
                )
                response.raise_for_status()
                result = response.json()
                ingestion_status.write(f"Indexed {result['page_count']} page(s)")
                ingestion_status.write(f"Created {result['chunk_count']} chunk(s)")
                if result["skipped_count"]:
                    ingestion_status.write(f"Skipped {result['skipped_count']} page(s)")
                ingestion_status.update(label="Website ready", state="complete", expanded=False)
                st.success(f"Added {result['source']['name']}")
            except httpx.HTTPStatusError as error:
                detail = error.response.json().get("detail", "The API rejected the website")
                ingestion_status.update(label="Website ingestion failed", state="error")
                st.error(str(detail))
            except httpx.RequestError:
                ingestion_status.update(label="API unavailable", state="error")
                st.error("Could not reach the ingestion API. Retry after the API is running.")

    st.divider()
    with st.expander("Connect PostgreSQL"):
        database_name = st.text_input("Source name", key="database_source_name")
        database_secret = st.text_input(
            "Connection secret environment variable",
            placeholder="SALES_DATABASE_URL",
            help="The API reads the connection URL from this environment variable.",
        )
        database_isolation = st.radio(
            "Tenant isolation",
            ["Dedicated database/schema", "Shared tables"],
        )
        shared_database = database_isolation == "Shared tables"
        table_specs = st.text_area(
            "Allowed tables",
            placeholder=(
                "public.sales:workspace_id\npublic.regions:workspace_id"
                if shared_database
                else "public.sales\npublic.regions"
            ),
            help=("One schema.table per line. Append :tenant_column for shared tables."),
        )
        if st.button(
            "Connect database",
            type="primary",
            use_container_width=True,
            disabled=not (database_name and database_secret and table_specs),
        ):
            tables: list[dict[str, str]] = []
            invalid_table = False
            for raw_spec in table_specs.splitlines():
                spec = raw_spec.strip()
                if not spec:
                    continue
                table_part, separator, tenant_column = spec.partition(":")
                schema_name, dot, table_name = table_part.partition(".")
                if not dot:
                    table_name = schema_name
                    schema_name = ""
                if shared_database and (not separator or not tenant_column.strip()):
                    invalid_table = True
                    break
                table: dict[str, str] = {"table_name": table_name.strip()}
                if schema_name.strip():
                    table["schema_name"] = schema_name.strip()
                if shared_database:
                    table["tenant_column"] = tenant_column.strip()
                tables.append(table)
            if invalid_table or not tables:
                st.error("Shared table entries must include :tenant_column.")
            else:
                try:
                    response = httpx.post(
                        f"{settings.api_base_url}/sources/database",
                        json={
                            "workspace_id": workspace_id,
                            "name": database_name,
                            "secret_env_var": database_secret,
                            "dialect": "postgresql",
                            "isolation_mode": "shared" if shared_database else "dedicated",
                            "tables": tables,
                        },
                        timeout=30,
                    )
                    response.raise_for_status()
                    result = response.json()
                    st.success(
                        f"Connected {result['source']['name']} with "
                        f"{len(result['tables'])} approved table(s)"
                    )
                except httpx.HTTPStatusError as error:
                    detail = error.response.json().get(
                        "detail", "The API rejected the database source"
                    )
                    st.error(str(detail))
                except httpx.RequestError:
                    st.error("Could not reach the API.")

st.subheader("Ask your knowledge base")
try:
    source_response = httpx.get(
        f"{settings.api_base_url}/sources",
        params={"workspace_id": workspace_id},
        timeout=5,
    )
    source_response.raise_for_status()
    ready_sources = [source for source in source_response.json() if source["status"] == "ready"]
except (httpx.HTTPStatusError, httpx.RequestError):
    ready_sources = []

if ready_sources:
    source_by_label = {
        f"{source['name']} · {source['config']['source_type']}": source for source in ready_sources
    }
    selected_source_labels = st.multiselect(
        "Sources",
        list(source_by_label),
        default=list(source_by_label),
        help=(
            "Automatic routing searches the selected documents, websites, CSVs, and databases. "
            "Select at most one database for structured questions."
        ),
    )
    question = st.chat_input(
        "Ask across your selected knowledge sources", disabled=not selected_source_labels
    )
    if question:
        selected_source_ids = [
            source_by_label[label]["source_id"] for label in selected_source_labels
        ]
        with st.chat_message("user"):
            st.write(question)
        try:
            response = httpx.post(
                f"{settings.api_base_url}/query",
                json={
                    "workspace_id": workspace_id,
                    "question": question,
                    "source_ids": selected_source_ids,
                    "retrieval_mode": "auto",
                },
                timeout=60,
            )
            response.raise_for_status()
            result = response.json()
            with st.chat_message("assistant"):
                st.write(result["answer"])
                citation_segments = result.get("citation_segments", [])
                st.caption("Sources")
                for citation in result["citations"]:
                    source_label = citation.get("source_title") or citation["source_type"]
                    with st.expander(
                        f"[{citation['citation_id']}] {source_label} · {citation['locator']}"
                    ):
                        supported_segments = [
                            segment["text"]
                            for segment in citation_segments
                            if citation["citation_id"] in segment["citation_ids"]
                        ]
                        for segment_text in supported_segments:
                            st.write(f"Supports: {segment_text}")
                        st.write(citation["excerpt"])
                        st.caption(
                            f"Retriever: {citation['retriever']} · Score: {citation['score']:.3f}"
                        )
                        locator_details = citation["locator_details"]
                        if locator_details["source_type"] == "website":
                            st.link_button("Open source page", locator_details["url"])
                        elif locator_details["source_type"] == "database" and (
                            fingerprint := locator_details.get("query_fingerprint")
                        ):
                            st.caption(f"Query fingerprint: {fingerprint[:12]}")
                routing_trace = result.get("routing_trace")
                if routing_trace:
                    with st.expander("How this question was routed"):
                        retrievers = ", ".join(routing_trace["selected_retrievers"])
                        st.write(f"**Intent:** {routing_trace['intent']}")
                        st.write(f"**Retrievers:** {retrievers}")
                        st.write(f"**Confidence:** {routing_trace['confidence']:.0%}")
                        if routing_trace.get("fallback_reason"):
                            st.caption(f"Fallback: {routing_trace['fallback_reason']}")
                        st.caption(f"Routing time: {routing_trace['routing_latency_ms']:.2f} ms")
        except httpx.HTTPStatusError as error:
            detail = error.response.json().get("detail", "The query was rejected")
            st.error(str(detail))
        except httpx.RequestError:
            st.error("Could not reach the query API.")
else:
    st.chat_input("Add a ready knowledge source before asking a question", disabled=True)
    st.info("Add a PDF, CSV, website, or approved PostgreSQL source to begin.")

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

st.subheader("Ask your knowledge base")
st.chat_input("Add and index a source before asking a question", disabled=True)
st.info("The project foundation is ready. Retrieval capabilities are coming next.")

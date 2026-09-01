"""Streamlit source-ingestion shell for Enterprise Knowledge Fusion RAG."""

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

st.subheader("Ask your knowledge base")
st.chat_input("Add and index a source before asking a question", disabled=True)
st.info("The project foundation is ready. Retrieval capabilities are coming next.")

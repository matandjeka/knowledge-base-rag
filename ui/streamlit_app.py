"""Multipage Streamlit application for Enterprise Knowledge Fusion RAG."""

import streamlit as st

from app.core.config import get_settings
from ui.api_client import RagApiClient
from ui.pages import chat_page, evaluation_page, retrieval_lab_page, settings_page, sources_page

st.set_page_config(page_title="Enterprise Knowledge Fusion RAG", page_icon="🔎", layout="wide")

settings = get_settings()
st.session_state.setdefault("rag_api_client", RagApiClient(settings.api_base_url))
st.session_state.setdefault("table_page_size", 25)
st.session_state.setdefault("expand_diagnostics", False)

with st.sidebar:
    st.header("Knowledge Fusion")
    st.caption("Workspace: `local`")

navigation = st.navigation(
    {
        "Knowledge": [
            st.Page(chat_page, title="Chat", icon="💬", default=True),
            st.Page(sources_page, title="Sources", icon="📚"),
        ],
        "Diagnostics": [
            st.Page(retrieval_lab_page, title="Retrieval Lab", icon="🧪"),
            st.Page(evaluation_page, title="Evaluation", icon="📊"),
            st.Page(settings_page, title="Settings", icon="⚙️"),
        ],
    }
)
navigation.run()

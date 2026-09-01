"""Initial Streamlit interface for Enterprise Knowledge Fusion RAG."""

import streamlit as st

st.set_page_config(page_title="Enterprise Knowledge Fusion RAG", page_icon="🔎", layout="wide")

st.title("Enterprise Knowledge Fusion RAG")
st.caption("Grounded answers across documents, websites, and structured data.")

with st.sidebar:
    st.header("Knowledge sources")
    st.info("Source management will be added in the ingestion phases.")

st.subheader("Ask your knowledge base")
st.chat_input("Add and index a source before asking a question", disabled=True)
st.info("The project foundation is ready. Retrieval capabilities are coming next.")

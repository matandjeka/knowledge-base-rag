# Advanced Multi-Source Enterprise RAG — Project Overview

## 1. Project Name
**Enterprise Knowledge Fusion RAG**

## 2. Purpose
Build an advanced enterprise knowledge-base question answering platform that ingests and reasons over multiple data sources:

- PDF and office-style documents
- Public or internal websites
- Structured CSV files and relational databases

The system combines multiple retrieval strategies, fuses and re-ranks candidate context, and sends only the most relevant evidence to an LLM. Answers must be precise, grounded, and include source citations.

## 3. Primary User Experience
A user can:

1. Upload one or more PDF files.
2. Enter a website URL to crawl or ingest.
3. Upload a CSV file or connect to a supported database.
4. Wait for ingestion/indexing to complete.
5. Ask a natural-language question.
6. Receive a cited answer based on the ingested enterprise knowledge.
7. Inspect the supporting passages, URLs, files, database rows, and confidence metadata.

## 4. Core Learning Goals
This project demonstrates practical knowledge of:

- Retrieval-Augmented Generation (RAG)
- Multi-source ingestion
- Semantic vector search
- Sentence-window retrieval
- Graph-based retrieval
- Hybrid retrieval and query routing
- Reciprocal Rank Fusion or weighted fusion
- Cross-encoder or LLM-based re-ranking
- Chunking and metadata design
- Embeddings
- Vector databases
- Knowledge graphs
- LlamaIndex and/or LangChain
- Hugging Face models
- Source attribution and citations
- Evaluation and observability
- Enterprise security patterns
- Streamlit or Gradio deployment
- Azure or Vercel-oriented deployment architecture

## 5. Proposed Technology Stack

### Backend / AI
- Python 3.12+
- FastAPI for service/API layer
- LlamaIndex as primary indexing/retrieval orchestration framework
- LangChain/LangGraph optionally for agentic workflows or advanced routing
- Hugging Face sentence-transformers for local embeddings and re-rankers
- OpenAI, Azure OpenAI, or compatible enterprise LLM provider for generation

### Retrieval
- FAISS for local development
- Pinecone for managed production vector storage
- Sentence-window retrieval through LlamaIndex sentence window node parser / metadata replacement
- Knowledge graph retrieval with Neo4j, LlamaIndex PropertyGraphIndex, or equivalent graph store
- BM25 optional lexical retriever for hybrid search

### Structured Data
- Pandas for CSV ingestion
- SQLAlchemy for relational database access
- PostgreSQL as recommended relational system of record

### Frontend
- Streamlit for fastest end-to-end prototype
- Gradio as alternative rapid demo UI
- Optional production UI: Next.js deployed to Vercel while Python APIs run separately on Azure, Container Apps, Functions, App Service, or another Python-compatible host

### Deployment
- Azure Container Apps or Azure App Service for Python application
- Azure Blob Storage for uploaded source files
- Azure Key Vault for secrets
- Azure Database for PostgreSQL if structured enterprise storage is needed
- Pinecone managed vector DB or Azure-compatible vector service
- Vercel can host a separate Next.js frontend; Streamlit itself should be hosted on a Python-compatible runtime rather than Vercel serverless functions

## 6. Functional Requirements

### FR-1: Data Source Management
The system shall allow users to register, upload, inspect, re-index, and remove knowledge sources.

### FR-2: PDF Ingestion
The system shall extract text, page metadata, document title, section information where possible, and create retrievable chunks.

### FR-3: Website Ingestion
The system shall fetch website content, clean navigation noise, preserve canonical URLs, and index page-level metadata.

### FR-4: CSV / Database Ingestion
The system shall support:
- CSV upload
- Schema inspection
- Row-to-document conversion for retrieval
- Optional natural-language-to-SQL retrieval for relational databases

### FR-5: Vector Retrieval
The system shall retrieve semantically similar passages based on an embedding query.

### FR-6: Sentence-Window Retrieval
The system shall retrieve focused sentence-level matches while expanding surrounding sentences for richer context.

### FR-7: Graph Retrieval
The system shall extract or infer entities and relationships and retrieve graph neighborhoods relevant to a user question.

### FR-8: Fusion
The system shall combine results from several retrievers and de-duplicate overlapping evidence.

### FR-9: Re-ranking
The system shall re-rank fused candidates using a cross-encoder or LLM-based re-ranker.

### FR-10: Answer Generation
The LLM shall answer only from retrieved evidence and clearly indicate when evidence is insufficient.

### FR-11: Citations
Each answer shall include citations pointing to the original source, including file/page, URL, CSV row identifier, or database table/record information where applicable.

### FR-12: Observability
The system shall record retrieval traces, latency, model usage, source coverage, and evaluation metrics.

## 7. Non-Functional Requirements

### Security
- Secrets stored outside source code
- Authentication-ready architecture
- Tenant isolation-ready metadata model
- Uploaded files validated by type and size
- Database credentials encrypted and never exposed to prompts

### Performance
- Interactive query response target: under 8 seconds for typical workloads
- Retrieval target: under 2 seconds where possible
- Asynchronous indexing for large knowledge sources

### Quality
- Every factual answer should be traceable to retrieved evidence
- Hallucination minimization through constrained prompting and retrieval confidence thresholds
- Evaluation dataset maintained for regression testing

### Scalability
- Pluggable vector store
- Pluggable graph store
- Stateless API tier where possible
- Background ingestion workers for production

## 8. Retrieval Pipeline

1. User submits question.
2. Query normalizer detects intent and optional filters.
3. Query router decides which retrievers to activate.
4. Vector retriever returns semantic candidates.
5. Sentence-window retriever returns localized context.
6. Graph retriever returns entity/relationship evidence.
7. Optional BM25 returns lexical candidates.
8. Candidate results are normalized into a common evidence schema.
9. Results are fused using RRF or weighted fusion.
10. Duplicate or near-duplicate chunks are collapsed.
11. Re-ranker scores the best candidates.
12. Context builder enforces token budget and source diversity.
13. LLM generates a grounded answer.
14. Citation formatter attaches source references.
15. Trace and evaluation metadata are persisted.

## 9. Suggested Repository Structure

```text
enterprise-rag/
├── app/
│   ├── api/
│   ├── core/
│   ├── ingestion/
│   ├── retrieval/
│   ├── reranking/
│   ├── generation/
│   ├── citations/
│   ├── evaluation/
│   ├── observability/
│   └── models/
├── ui/
│   └── streamlit_app.py
├── tests/
├── scripts/
├── data/
├── docs/
├── docker/
├── .env.example
├── pyproject.toml
└── README.md
```

## 10. MVP Definition of Done
The MVP is complete when a user can upload a PDF, provide a website, upload a CSV, index all three, ask a question, and receive a grounded answer with citations produced from a fused and re-ranked retrieval pipeline.

## 11. Advanced Definition of Done
The advanced version additionally includes:

- Knowledge graph creation and graph retrieval
- Query routing
- Hybrid lexical + semantic retrieval
- Cross-encoder re-ranking
- Multi-tenant-ready metadata
- Evaluation dashboard
- Retrieval traces
- Azure production deployment
- Optional Next.js/Vercel frontend
- Admin source-management interface

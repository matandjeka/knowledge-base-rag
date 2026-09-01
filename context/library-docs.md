# Advanced Multi-Source Enterprise RAG — Library Documentation

## 1. Purpose
Document the main libraries used in the project and the responsibility assigned to each.

## 2. LlamaIndex
### Role
Primary framework for:
- document ingestion abstractions
- node parsing
- vector indexes
- sentence-window retrieval
- property graph indexing
- query engines
- response synthesis
- citation-aware workflows

### Use in This Project
Recommended as the central RAG abstraction layer.

Potential modules/features:
- `VectorStoreIndex`
- sentence window node parsing
- metadata replacement post-processing
- `PropertyGraphIndex`
- retriever composition
- node post-processors / re-rankers

### Rule
Do not allow LlamaIndex framework objects to leak into every layer. Wrap framework code inside adapter modules so the application can evolve.

## 3. LangChain / LangGraph
### Role
Optional orchestration layer.

Use when implementing:
- complex query routing
- agent/tool workflows
- SQL tool calling
- retry/state machines
- multi-step research workflows

### Rule
Do not add LangGraph just because it is popular. Use it when stateful workflow behavior is needed.

## 4. Hugging Face Transformers
### Role
Use for:
- local embedding models where applicable
- cross-encoder re-rankers
- optional local LLM experiments
- entity extraction models

## 5. sentence-transformers
### Role
Preferred library for local text embeddings and many cross-encoder re-rankers.

Example categories:
- BGE embeddings
- E5 embeddings
- MiniLM development models

Choose an embedding model based on:
- domain
- retrieval quality
- vector dimension
- inference speed
- license

## 6. FAISS
### Role
Development vector index.

Advantages:
- local
- fast
- free
- simple

Limitations:
- not itself a full multi-tenant managed database
- persistence/metadata capabilities require application support
- production scaling is operationally heavier

## 7. Pinecone
### Role
Managed production vector database option.

Use for:
- persistent vector indexes
- metadata filtering
- namespaces / environment separation
- scalable production retrieval

Recommended metadata:

```text
workspace_id
source_id
source_type
document_id
page_number
url
table_name
row_id
security_labels
```

## 8. Neo4j
### Role
Production knowledge graph option.

Use for:
- entities
- relationships
- multi-hop questions
- organizational ownership questions
- policy dependency questions

Example Cypher use cases:

```cypher
MATCH (p:Policy)-[:APPLIES_TO]->(d:Department)
WHERE d.name = $department
RETURN p
```

## 9. Pandas
### Role
CSV ingestion, schema inspection, profiling, and row normalization.

Do not use Pandas as a production database.

## 10. SQLAlchemy
### Role
Database connectivity and safe structured access abstraction.

Recommended:
- connection pooling
- read-only accounts
- allowlisted schemas

## 11. FastAPI
### Role
Backend API service.

Use for:
- source lifecycle endpoints
- query endpoints
- health checks
- authentication integration
- async orchestration

Recommended support libraries:
- Pydantic
- Uvicorn
- httpx

## 12. Streamlit
### Role
Primary demonstration UI.

Use for:
- file upload
- URL input
- CSV preview
- chat
- source inspector
- retrieval debug view

Good fit for portfolio demonstration because it minimizes frontend boilerplate.

## 13. Gradio
### Role
Alternative rapid interface.

Prefer when:
- building a model demo quickly
- needing simple upload/input/output components

Use Streamlit if the app needs a more dashboard-like enterprise experience.

## 14. BeautifulSoup / Trafilatura / Web Loaders
### Role
Website extraction.

Preferred strategy:
- Use a robust content extraction library to remove navigation and boilerplate.
- Preserve original URL and page title.
- Respect robots policies and organization crawl policies.

## 15. PDF Libraries
Potential options:
- PyMuPDF
- pypdf
- LlamaIndex PDF readers

For advanced enterprise PDFs, consider OCR/document intelligence only when needed.

## 16. Pydantic
### Role
Request/response contracts, configuration, evidence objects, and validation.

## 17. Evaluation Libraries
Potential options:
- Ragas
- DeepEval
- custom deterministic retrieval metrics

The project should not rely exclusively on LLM-as-judge. Include objective retrieval metrics.

## 18. Observability Libraries
Potential options:
- OpenTelemetry
- Langfuse
- Arize Phoenix
- application metrics/logging stack

Capture retrieval traces without leaking sensitive source content unnecessarily.

## 19. Recommended Initial Dependency Set

```text
fastapi
uvicorn
streamlit
pydantic
pydantic-settings
llama-index
sentence-transformers
transformers
faiss-cpu
pandas
sqlalchemy
httpx
beautifulsoup4
trafilatura
pymupdf
rank-bm25
neo4j
pytest
pytest-asyncio
ruff
mypy
```

Add Pinecone and Azure SDK libraries when moving to production deployment.

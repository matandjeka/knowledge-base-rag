# Advanced Multi-Source Enterprise RAG — Build Plan

## Phase 0 — Project Foundation

### Goals
Create repository structure, configuration, standards, and base models.

### Tasks
- Create Python project
- Configure `pyproject.toml`
- Add Ruff, formatting, typing, pytest
- Create `.env.example`
- Add base Pydantic models
- Add structured logging
- Create FastAPI health endpoint
- Create initial Streamlit shell

### Exit Criteria
- API runs
- UI runs
- tests/lint run successfully

---

## Phase 1 — Canonical Source Model

### Goals
Create a common representation for all source types.

### Tasks
- Create `Source`
- Create `RawDocument`
- Create `NormalizedDocument`
- Create `Evidence`
- Create source registry repository
- Add source lifecycle statuses

### Exit Criteria
All connectors can eventually emit the same normalized model.

---

## Phase 2 — PDF Ingestion

### Tasks
- Implement PDF upload
- Store original PDF
- Extract page text
- Preserve page numbers
- Normalize whitespace
- Create chunks
- Persist source metadata
- Add indexing status UI

### Test Questions
- “What does the document say about X?”
- “Which page mentions Y?”

### Exit Criteria
PDF can be uploaded, indexed, retrieved, and cited by page.

---

## Phase 3 — Website Ingestion

### Tasks
- URL validation
- Same-domain crawl option
- Crawl page limits
- HTML extraction
- Boilerplate cleaning
- Canonical URL preservation
- Page title extraction
- Chunking and indexing

### Exit Criteria
A user can provide a website and ask cited questions against the indexed pages.

---

## Phase 4 — CSV Ingestion

### Tasks
- Upload CSV
- Preview schema
- Select text columns
- Select metadata columns
- Convert rows into normalized documents
- Preserve row IDs
- Index rows

### Exit Criteria
Answers can cite CSV row identifiers.

---

## Phase 5 — Baseline Vector RAG

### Tasks
- Add embedding service interface
- Implement Hugging Face embedding adapter
- Implement FAISS vector store adapter
- Add top-k vector retrieval
- Build initial grounded answer prompt
- Add source citations

### Exit Criteria
One query can retrieve relevant evidence across all three source types.

---

## Phase 6 — Sentence-Window Retrieval

### Tasks
- Sentence-level parsing
- Store neighbor-window metadata
- Implement sentence retriever
- Expand matched sentence to surrounding window
- Return evidence using common schema

### Evaluation
Compare against baseline chunk retrieval on paragraph-specific questions.

### Exit Criteria
Sentence-window retrieval can be enabled independently.

---

## Phase 7 — Knowledge Graph Retrieval

### Tasks
- Define graph schema
- Extract entities
- Extract relationships
- Build graph index
- Add graph retriever
- Map graph evidence back to source citations

### Example Entity Types
- Person
- Department
- Policy
- Product
- Project
- Contract
- Regulation
- Location

### Exit Criteria
The system can answer multi-hop relationship questions.

---

## Phase 8 — Optional BM25 / Lexical Retriever

### Tasks
- Add BM25 index
- Return evidence through common retriever contract
- Test identifier-heavy queries

### Exit Criteria
Exact identifiers and acronyms outperform vector-only retrieval where appropriate.

---

## Phase 9 — Fusion Layer

### Tasks
- Normalize evidence IDs
- De-duplicate candidates
- Implement RRF
- Add weighted fusion option
- Track rank contribution by retriever

### Exit Criteria
A single fused candidate list is produced from several retrievers.

---

## Phase 10 — Re-ranking

### Tasks
- Add Hugging Face cross-encoder re-ranker
- Re-rank top fused candidates
- Enforce source diversity
- Add latency measurements

### Exit Criteria
Top final context is measurably more relevant than fused-only results.

---

## Phase 11 — Structured Database Retrieval

### Tasks
- SQLAlchemy connection layer
- Read-only connection rules
- Schema introspection
- Allowlist tables
- Natural-language-to-SQL tool
- SQL validation
- Result-to-evidence conversion

### Exit Criteria
The user can ask aggregation/filter questions against a relational database safely.

---

## Phase 12 — Query Router

### Tasks
Classify or infer when to use:
- vector
- window
- graph
- lexical
- SQL

Start with rules; later test LLM-based routing.

### Exit Criteria
Simple questions avoid unnecessary retrievers without harming quality.

---

## Phase 13 — Advanced Citation Engine

### Tasks
- Stable citation IDs
- Inline citation markers
- Citation inspector
- PDF page locator
- URL locator
- CSV row locator
- DB table/row locator

### Exit Criteria
Every important factual claim maps to inspectable evidence.

---

## Phase 14 — Evaluation Framework

### Tasks
- Create 30-50 benchmark questions
- Label expected sources
- Add recall@k
- Add MRR
- Add faithfulness evaluation
- Add citation accuracy evaluation
- Compare retrieval configurations

### Exit Criteria
Retrieval changes are evaluated before deployment.

---

## Phase 15 — Streamlit Application

### Pages/Tabs
1. Chat
2. Sources
3. Retrieval Lab
4. Evaluation
5. Settings

### Features
- PDF upload
- Website input
- CSV upload
- DB configuration
- Chat
- Citations
- Source inspector
- Retrieval trace

### Exit Criteria
All MVP features are available from the web interface.

---

## Phase 16 — Production Persistence

### Tasks
Replace local components:
- FAISS → Pinecone
- local files → Azure Blob Storage
- local metadata DB → PostgreSQL
- in-memory graph → Neo4j

### Exit Criteria
Restarting services does not lose indexed enterprise data.

---

## Phase 17 — Azure Deployment

### Tasks
- Dockerize API/UI
- Deploy Python services to Azure Container Apps or App Service
- Configure Azure Blob Storage
- Configure Key Vault
- Configure managed PostgreSQL
- Add application health checks
- Configure logs/metrics

### Exit Criteria
Public or private enterprise deployment works end-to-end.

---

## Phase 18 — Optional Vercel Frontend

### Tasks
- Build Next.js frontend
- Deploy to Vercel
- Connect to Azure-hosted FastAPI
- Implement auth
- Preserve citation/source UX

### Exit Criteria
Modern production UI communicates securely with the RAG backend.

---

## Phase 19 — Enterprise Hardening

### Tasks
- Tenant isolation
- RBAC
- audit logs
- retention policies
- source-level security labels
- prompt injection defenses
- website allowlists
- PII-aware logging
- rate limiting

### Exit Criteria
Architecture is suitable for an enterprise portfolio demonstration and can be extended to real organizational use.

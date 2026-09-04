# Advanced Multi-Source Enterprise RAG — Progress Tracker

## Overall Status
**Last Reviewed:** September 4, 2026

**Last Completed Implementation Phase:** Phase 10 — Re-ranking

**Next Phase:** Phase 11 — Structured Database Retrieval (ready to architect)

**Last Phase With All Exit Criteria Satisfied:** Phase 10 — Re-ranking

Phases 2–10 now provide ingestion, coordinated baseline, sentence-window, and lexical index
generations for FAISS/Pinecone plus durable local BM25 storage, OpenAI-assisted knowledge-graph
extraction, durable local graph generations, independently selectable or concurrently fused
vector/window/graph/lexical retrieval, optional cross-encoder re-ranking with source diversity,
grounded answers, and multi-source provenance. Advanced citation rendering and source inspection
remain deferred to Phase 13.

**Build-plan alignment:** Phases 0–10 are complete. Phase 11 structured database retrieval has not
started. Tracker sections are grouped by subsystem, so their section numbers do not map one-to-one
to the phase numbers in `build-plan.md`.

**Verification:** Ruff formatting and lint pass, mypy passes across 78 source files, and pytest
reports `142 passed, 1 skipped`. The skipped test is the opt-in live OpenAI graph integration test.

## Status Legend
- [ ] Not started
- [~] In progress
- [x] Complete
- [!] Blocked

## 1. Foundation
- [x] Create Git repository
- [x] Create Python virtual environment
- [x] Create `pyproject.toml`
- [x] Configure Ruff
- [x] Configure type checking
- [x] Configure pytest
- [x] Create `.env.example`
- [x] Create FastAPI app
- [x] Create Streamlit app shell
- [x] Add structured logging
- [x] Create FastAPI health endpoint

## 2. Domain Models
- [x] Source model
- [x] SourceConfig model
- [x] RawDocument model
- [x] NormalizedDocument model
- [x] Evidence model
- [x] Add source lifecycle statuses
- [x] Create source registry repository
- [x] QueryRequest model
- [x] QueryResponse model
- [x] Citation model

## 3. PDF Source
- [x] PDF uploader
- [x] File validation
- [x] PDF parsing
- [x] Page metadata
- [x] Chunking
- [x] Indexing
- [x] Page citations
- [x] PDF ingestion tests

**Implementation status:** Complete

**Exit criteria:** Complete — PDF evidence is indexed, retrieved, and cited by page.

## 4. Website Source
- [x] URL validation
- [x] Crawler
- [x] Crawl limits
- [x] Same-domain option
- [x] Text cleaning
- [x] URL metadata
- [x] Indexing
- [x] Website citation tests

**Implementation status:** Complete

**Exit criteria:** Complete — website evidence is indexed, retrieved, and cited by URL.

## 5. CSV Source
- [x] CSV uploader
- [x] Schema preview
- [x] Text-column selection
- [x] Metadata-column selection
- [x] Row normalization
- [x] Indexing
- [x] Row citations

**Implementation status:** Complete

**Exit criteria:** Complete — CSV rows are indexed, retrieved, and cited by stable row ID.

## 6. Database Source
- [ ] SQLAlchemy adapter
- [ ] Read-only connection
- [ ] Schema discovery
- [ ] Table allowlist
- [ ] SQL generation
- [ ] SQL safety validator
- [ ] Structured result evidence
- [ ] DB citations

## 7. Embeddings / Vector Store
- [x] Embedding interface
- [x] Hugging Face embeddings
- [x] FAISS adapter
- [x] Pinecone adapter
- [x] Metadata filtering
- [x] Vector retrieval tests

## 8. Sentence Window Retrieval
- [x] Sentence parser
- [x] Window metadata
- [x] Window retriever
- [x] Metadata replacement
- [x] Evaluation vs baseline

**Implementation status:** Complete

**Exit criteria:** Complete — sentence-window retrieval can be selected independently for a
query, expands focused matches within their normalized document boundaries, and preserves
source-specific citation locators across FAISS and Pinecone. A committed three-case benchmark
compares baseline and sentence-window hit rate, MRR, citation accuracy, context expansion, and
latency on paragraph-specific questions.

## 9. Graph Retrieval
- [x] Graph schema
- [x] Entity extraction
- [x] Relationship extraction
- [x] Graph persistence
- [x] Graph retriever
- [x] Multi-hop test dataset
- [x] Graph-to-source citations

**Implementation status:** Complete

**Exit criteria:** Complete — explicit graph indexing creates a validated, checksummed workspace
generation, and deterministic two-hop retrieval answers the committed five-case benchmark with
complete edge-level citations across PDF, website, and CSV provenance.

## 10. Lexical Retrieval
- [x] BM25 index
- [x] BM25 retriever
- [x] Identifier query tests

**Implementation status:** Complete

**Exit criteria:** Complete — durable workspace BM25 generations preserve exact identifiers and
source locators, expose independently selectable lexical retrieval, and outperform vector-only
retrieval on the committed six-case PDF, website, and CSV identifier benchmark for hit rate and
MRR with complete citation accuracy.

## 11. Fusion
- [x] Common retriever interface
- [x] Evidence normalization
- [x] De-duplication
- [x] Reciprocal Rank Fusion
- [x] Weighted fusion
- [x] Fusion tests

**Implementation status:** Complete

**Exit criteria:** Complete — fused queries concurrently retrieve from an explicit set of
strategies, collapse candidates with stable canonical identities, and produce one deterministic
RRF or weighted-RRF list. Every result exposes reconstructable per-retriever rank contributions,
and a committed six-case benchmark verifies improved hit rate and MRR with complete citations.

## 12. Re-ranking
- [x] Cross-encoder model
- [x] Re-ranker service
- [x] Top-N configuration
- [x] Source diversity constraint
- [x] Latency benchmarking
- [x] Re-ranking evaluation

**Implementation status:** Complete

**Exit criteria:** Complete — fused candidates can be re-ranked through a lazy Hugging Face
cross-encoder, selected with a deterministic two-pass source-diversity policy, and returned with
stable citations plus inspectable fusion, model-score, rank, and latency provenance. A committed
eight-case graded benchmark improves MRR and NDCG over fused-only ordering.

## 13. Generation
- [x] Grounded system prompt
- [x] Context builder
- [ ] Token-budget management
- [x] Generation provider abstraction
- [x] Insufficient-evidence response
- [x] Structured response contract

## 14. Citations
- [x] Stable citation IDs
- [x] PDF locator
- [x] Website locator
- [x] CSV locator
- [ ] Database locator
- [ ] Inline citation renderer
- [ ] Source inspector
- [x] Baseline citation accuracy tests
- [x] Graph edge-to-source citation mapping

## 15. Query Routing
- [ ] Rules-based router
- [ ] Retriever selection
- [ ] SQL intent detection
- [ ] Graph intent detection
- [ ] Evaluation of routing impact

## 16. Streamlit UI
- [x] App shell
- [x] Sidebar
- [ ] Source manager
- [x] PDF upload
- [x] Website form
- [ ] CSV upload
- [ ] DB form
- [ ] Chat
- [ ] Citation badges
- [ ] Source inspector
- [ ] Retrieval trace panel
- [ ] Evaluation dashboard

## 17. Evaluation
- [~] Golden question set (28 focused Phase 6–10 cases; 30–50 case suite deferred)
- [~] Expected source labels (implemented for window, graph, lexical, fusion, and re-ranking)
- [ ] Recall@K
- [ ] Precision@K
- [x] MRR
- [x] NDCG
- [ ] Faithfulness
- [x] Citation accuracy
- [x] Latency tracking
- [ ] Cost tracking

## 18. Observability
- [ ] Trace IDs
- [x] Structured logs
- [ ] Retriever timings
- [x] Fusion trace
- [x] Re-ranker trace
- [ ] LLM token usage
- [ ] User feedback capture

## 19. Security
- [ ] JWT/OIDC integration
- [ ] Workspace/tenant model
- [ ] Source authorization
- [ ] Retrieval metadata filters
- [x] File validation
- [x] Crawl allowlist
- [ ] Secret management
- [ ] SQL read-only enforcement
- [ ] Prompt injection mitigation

## 20. Local Deployment
- [ ] Dockerfile
- [ ] Docker Compose
- [ ] Local PostgreSQL
- [ ] Local Neo4j option
- [x] Health checks

## 21. Azure Deployment
- [ ] Azure subscription/resource group
- [ ] Azure Container Apps or App Service
- [ ] Blob Storage
- [ ] Key Vault
- [ ] PostgreSQL
- [ ] Environment configuration
- [ ] Logging/monitoring
- [ ] Deployment workflow

## 22. Vercel Frontend — Optional
- [ ] Next.js UI
- [ ] API client
- [ ] Authentication
- [ ] Deploy to Vercel
- [ ] Connect to Azure backend

## 23. Portfolio Readiness
- [x] Architecture diagram
- [ ] Demo dataset
- [ ] Demo script
- [ ] Screenshot set
- [ ] Recorded walkthrough
- [x] README
- [ ] Resume project bullets
- [ ] Interview explanation

## Milestones

### Milestone A — Basic Multi-Source RAG
PDF + website + CSV + vector retrieval + citations.

Status: [x]

Completed through Phases 2–5 with cross-source vector retrieval, grounded answers, and
source-specific PDF page, website URL, and CSV row citations. Advanced inline rendering
and evidence inspection remain part of Phase 13 and are not required for this milestone.

### Milestone B — Advanced Retrieval
Sentence windows + graph + BM25 + fusion + re-ranking.

Status: [ ]

### Milestone C — Enterprise UX
Streamlit source manager + chat + evidence inspector + evaluation.

Status: [ ]

### Milestone D — Production Deployment
Azure persistence and deployment, optionally Vercel frontend.

Status: [ ]

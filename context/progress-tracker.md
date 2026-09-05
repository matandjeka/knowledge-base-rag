# Advanced Multi-Source Enterprise RAG — Progress Tracker

## Overall Status
**Last Reviewed:** September 4, 2026

**Last Completed Implementation Phase:** Phase 14 — Evaluation Framework

**Next Phase:** Phase 15 — Streamlit Application (ready to architect)

**Last Phase With All Exit Criteria Satisfied:** Phase 14 — Evaluation Framework

Phases 2–12 now provide ingestion, coordinated baseline, sentence-window, and lexical index
generations for FAISS/Pinecone plus durable local BM25 storage, OpenAI-assisted knowledge-graph
extraction, durable local graph generations, independently selectable or concurrently fused
vector/window/graph/lexical retrieval, optional cross-encoder re-ranking with source diversity,
PostgreSQL-first structured retrieval with AST-validated read-only SQL and server-enforced tenant
filters, deterministic source-aware query routing, grounded answers, typed source locators,
validated inline markers, bounded citation inspection, and versioned deployment-gate evaluation.

**Build-plan alignment:** Phases 0–14 are complete. Phase 15 Streamlit application is next. Tracker
sections are grouped by subsystem, so their section numbers do not map one-to-one to the phase
numbers in `build-plan.md`.

**Verification:** Ruff formatting and lint pass, strict mypy passes across 104 source files, and
pytest reports `197 passed, 1 skipped`. The skipped test is the opt-in live OpenAI graph
integration test.

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
- [x] SQLAlchemy adapter
- [x] Read-only connection
- [x] Schema discovery
- [x] Table allowlist
- [x] SQL generation
- [x] SQL safety validator
- [x] Structured result evidence
- [x] DB citations

**Implementation status:** Complete

**Exit criteria:** Complete — one explicitly selected, workspace-scoped PostgreSQL source can
answer aggregation and filtering questions through schema-aware structured SQL generation.
Credentials remain environment-only; SQL is parsed, allowlist-checked, parameterized, tenant-
filtered, row-limited, and executed in a read-only transaction. SQLite provides the deterministic
end-to-end test adapter, and database results retain table, record, query-fingerprint, row-count,
and latency provenance.

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
- [x] Database locator
- [x] Inline citation renderer
- [x] Source inspector
- [x] Baseline citation accuracy tests
- [x] Graph edge-to-source citation mapping

**Implementation status:** Complete

**Exit criteria:** Complete — request-local IDs are deterministically assigned from final evidence
order and exact canonical citation identities. Every citation exposes a strict source-specific
locator while retaining its display locator, generated factual paragraphs must end in valid
inline markers, and generators explicitly declare whether evidence is sufficient. Streamlit
provides bounded citation inspection without secondary source or database reads. A committed
all-source benchmark and focused invalid-output, duplicate, graph, multi-citation, URL-safety, and
compatibility tests enforce complete locator and marker accuracy.

## 15. Query Routing
- [x] Rules-based router
- [x] Retriever selection
- [x] SQL intent detection
- [x] Graph intent detection
- [x] Evaluation of routing impact

**Implementation status:** Complete

**Exit criteria:** Complete — opt-in automatic routing selects the smallest reliable SQL,
graph-plus-vector, lexical-plus-vector, sentence-window-plus-vector, or default-fusion plan using
deterministic confidence and winning-margin rules. Responses expose routing traces, logs exclude
question text, unavailable graph indexes safely fall back to default fusion, and the committed
12-question benchmark meets the plan-accuracy, SQL-safety, quality, citation, and latency gates.

## 16. Streamlit UI
- [x] App shell
- [x] Sidebar
- [ ] Source manager
- [x] PDF upload
- [x] Website form
- [x] CSV upload
- [x] DB form
- [x] Chat
- [x] Citation badges
- [x] Source inspector
- [x] Retrieval trace panel
- [ ] Evaluation dashboard

## 17. Evaluation
- [x] Golden question set (40-case unified suite plus focused regression benchmarks)
- [x] Expected source labels
- [x] Recall@K
- [x] Precision@K
- [x] MRR
- [x] NDCG
- [x] Faithfulness
- [x] Citation accuracy
- [x] Latency tracking
- [~] Cost tracking (optional token and cost observations; provider accounting deferred)

**Implementation status:** Complete

**Exit criteria:** Complete — one strict, versioned 40-case benchmark covers every source type and
routing intent with evidence-level relevance, locator, fact, contradiction, safety, and
insufficient-evidence labels. The deterministic tier executes the application `QueryService`
against a committed in-memory corpus, while the explicitly enabled live tier uses configured
application dependencies and optionally structured OpenAI faithfulness judgments. Unified metrics,
source-specific slices, complete candidate-versus-baseline gates, atomic redacted JSON reports, and
the baseline-required `rag-evaluate` CLI ensure retrieval changes are evaluated before deployment.
Existing specialized graph, routing, re-ranking, retrieval, and citation benchmarks remain
independent regression suites.

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
- [~] Secret management (environment-backed references implemented; managed vault deferred)
- [x] SQL read-only enforcement
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

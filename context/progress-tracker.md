# Advanced Multi-Source Enterprise RAG — Progress Tracker

## Overall Status
**Last Reviewed:** September 7, 2026

**Last Completed Implementation Phase:** Phase 16 — Production Persistence

**Current Phase:** Phase 18 — Optional Vercel Frontend (in progress). The Vercel architecture was
approved September 7, 2026 (`context/design/phase18.md`) and implementation covers the frontend,
hosted-model, auth, and durable-job subsystems. A code review on September 7, 2026 and its
follow-up fixes (branch `fix/phase18-review`) resolved the broken durable-job tests, tightened
config/isolation guardrails, rewrote migration `0002` as a self-contained snapshot, and shortened
the job-step database transaction. It is still not complete: no live Vercel deployment or cloud
verification has been performed.

**Skipped Phase:** Phase 17 — Azure Deployment, per user instruction on September 7, 2026.
No Azure deployment implementation or provisioning was performed.

**Exit-Criteria Verification:** Automated local coverage exists through Phase 16. Live model
quality, full UI ingestion/query reliability, and full-stack production restart recovery remain
unverified; Phase 16 is implementation-complete, not deployment-validated.

Phases 2–12 now provide ingestion, coordinated baseline, sentence-window, and lexical index
generations for FAISS/Pinecone plus durable local BM25 storage, OpenAI-assisted knowledge-graph
extraction, durable local graph generations, independently selectable or concurrently fused
vector/window/graph/lexical retrieval, optional cross-encoder re-ranking with source diversity,
PostgreSQL-first structured retrieval with AST-validated read-only SQL and server-enforced tenant
filters, deterministic source-aware query routing, grounded answers, typed source locators,
validated inline markers, bounded citation inspection, versioned deployment-gate evaluation, and a
five-page Streamlit interface for chat, source management, retrieval experiments, evaluation
inspection, and safe effective settings.

**Build-plan alignment:** Phases 0–16 are implemented with passing automated regression coverage.
Phase 17 Azure Deployment is skipped. Phase 18 (Optional Vercel Frontend) is in progress: the
backend-hosting question was resolved by targeting two Vercel projects (FastAPI API + Next.js
frontend) with Vercel Blob, hosted Voyage embeddings/reranking, a new 1,024-dim Pinecone index,
self-registration auth with per-client workspace isolation, and Vercel Workflow-orchestrated
durable jobs. Phase 19 remains unstarted.
Tracker sections are grouped by subsystem, so their section numbers do not map one-to-one to the
phase numbers in `build-plan.md`. Production mode now requires the complete durable stack while
the existing local adapters remain available for development, tests, and rollback.

**Verification (rerun September 7, 2026, after the Phase 18 review fixes):** `ruff check .`
passes; `ruff format --check .` reports 141 files already formatted; strict `mypy app` passes
across 101 source files; `pytest -q` reports **`230 passed, 1 skipped`** in ~27 seconds. The
skipped test is the opt-in live OpenAI graph integration test. `tests/test_phase18_jobs.py` now
parametrizes the CSV/PDF durable-job case and exercises the retry/publish path; new tests cover
the `vercel_blob`-in-production auth guardrail and nested-`workspace_id` rejection. Frontend
`npm run typecheck` and `node --test` pass. `alembic upgrade --sql` renders migration `0002`
cleanly with a single head. The API was run locally (`uvicorn app.main:app`, `OMP_NUM_THREADS=1`):
14/14 route smoke checks pass and a CSV source was ingested and answered with a grounded row
citation. These checks still do not exercise any live Vercel/Voyage/Blob/Workflow path.

## Open Validation Work

- [x] Local API native crash (exit code 139) while loading `BAAI/bge-small-en-v1.5`: resolved by
  running with `OMP_NUM_THREADS=1`. On September 7, 2026 the API booted clean, loaded the
  SentenceTransformer, ingested a CSV source end to end (status reached `ready` after a slow but
  successful CPU embed/FAISS build on this x86_64 Mac), and answered a grounded query with a
  correct `[S1]` CSV-row citation. 14/14 route smoke checks passed.
- [ ] Re-run PDF and website ingestion plus a cited query through the Streamlit UI (build-plan
  Phases 2–4 and 15 runtime acceptance). CSV ingestion + cited retrieval verified via the API.
- [ ] Verify model-backed quality comparisons, including re-ranking improvement over fused-only
  results (Phase 10). Deterministic fixtures and model doubles validate contracts and metrics;
  they do not establish live cross-encoder quality.
- [ ] Verify Phase 16 restart recovery using PostgreSQL, Azure Blob, Pinecone, and Neo4j together,
  including indexed-data retrieval after restart. Current persistence tests use SQLite,
  in-memory coordination, and simulated Blob storage; cloud provisioning is Phase 17 work.
- [-] Deferred with skipped Phase 17: create Docker/deployment artifacts and configure Azure resources, Key Vault, monitoring,
  and an end-to-end deployment smoke test (Phase 17). `docker/` and `scripts/` contain only placeholders.
- [x] Fix the broken `tests/test_phase18_jobs.py` cases (CSV/PDF parametrize; graph-test source
  lifecycle), restoring green `ruff check` and `pytest`. Done on `fix/phase18-review`.
- [x] Phase 18 review follow-ups: require `AUTH_ENABLED` for `vercel_blob` in production; scan the
  whole JSON body for `workspace_id` in `authorize`; restore optional (uncapped) pagination on
  `GET /sources/{id}/documents`; rewrite migration `0002` as explicit `op.create_table`; bound the
  authenticated evaluation-report listing; shorten the `step_job` transaction so no row lock is
  held across provider calls; drop the `app/main.py` E402 import workaround.
- [x] Runtime fixes found while running the app: `GET /jobs`, `/jobs/{id}`, resume, and cancel
  returned HTTP 500 (constructing the metadata engine before the auth check) — now a clean 503
  when auth is disabled; `/internal/*` returned 500 when auth is disabled — the workflow-secret
  check now runs regardless of `AUTH_ENABLED` (clean 401); `GET /auth/me` returned 500 without a
  session — now 401.
- [ ] Provision the Phase 18 Vercel stack (two projects, private Blob, PostgreSQL `rag` schema,
  new 1,024-dim Voyage Pinecone index, Neo4j, Voyage key, mail webhook) and run the
  `docs/phase18-deployment.md` acceptance checklist: client isolation, session/refresh rotation,
  direct Blob uploads >4.5 MB, retry idempotency, interrupted-job recovery, and retrieval after
  restart/redeploy.
- [ ] Run live Voyage embedding/reranking quality comparisons and calibrate similarity thresholds
  (the BGE 0.70 cutoff is not a validated Voyage value); reindex against the new index.
- [ ] Verify Vercel Workflow dispatch, concurrent job-step delivery against PostgreSQL (the step
  fence now uses two short transactions rather than a lock held across provider calls), and
  transactional mail delivery in a configured preview environment (offline tests use SQLite doubles).

The phase table below tracks implementation completion. Subsystem exit-criteria summaries
describe automated acceptance coverage, subject to the live-validation gaps above.

## Build-Plan Phase Status

| Build-plan phase | Status |
| --- | --- |
| Phase 0 — Project Foundation | [x] Complete |
| Phase 1 — Canonical Source Model | [x] Complete |
| Phase 2 — PDF Ingestion | [x] Complete |
| Phase 3 — Website Ingestion | [x] Complete |
| Phase 4 — CSV Ingestion | [x] Complete |
| Phase 5 — Baseline Vector RAG | [x] Complete |
| Phase 6 — Sentence-Window Retrieval | [x] Complete |
| Phase 7 — Knowledge Graph Retrieval | [x] Complete |
| Phase 8 — Optional BM25 / Lexical Retriever | [x] Complete |
| Phase 9 — Fusion Layer | [x] Complete |
| Phase 10 — Re-ranking | [x] Complete |
| Phase 11 — Structured Database Retrieval | [x] Complete |
| Phase 12 — Query Router | [x] Complete |
| Phase 13 — Advanced Citation Engine | [x] Complete |
| Phase 14 — Evaluation Framework | [x] Complete |
| Phase 15 — Streamlit Application | [x] Complete |
| Phase 16 — Production Persistence | [x] Implemented; live stack restart verification pending |
| Phase 17 — Azure Deployment | [-] Skipped by user; not implemented |
| Phase 18 — Optional Vercel Frontend | [~] In progress — code across frontend/auth/hosted-models/jobs; offline suite green after review fixes; not deployed or cloud-verified |
| Phase 19 — Enterprise Hardening | [ ] Not started |

## Status Legend
- [ ] Not started
- [~] In progress
- [x] Complete
- [!] Blocked
- [-] Skipped / deferred by user

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
- [x] Source manager
- [x] PDF upload
- [x] Website form
- [x] CSV upload
- [x] DB form
- [x] Chat
- [x] Citation badges
- [x] Source inspector
- [x] Retrieval trace panel
- [x] Evaluation dashboard

**Implementation status:** Complete

**Exit criteria:** Complete — the native Streamlit multipage shell exposes Chat, Sources,
Retrieval Lab, Evaluation, and Settings through one typed FastAPI client. The source manager lists,
inspects, registers, and explicitly re-indexes supported sources; session-scoped retrieval
experiments expose valid query controls without mutating backend defaults; and read-only,
path-safe evaluation APIs power report metrics, slices, case diagnostics, and deployment gates.
Effective settings are secret-safe, the workspace remains explicitly fixed to `local`, and the
application passes API, UI-boundary, strict typing, formatting, full regression, and server-startup
checks. Post-review hardening adds typed ingestion contracts, operation-specific timeouts,
short-lived read caches with mutation invalidation, functional display preferences, source-aware
SQL and inspection controls, end-to-end experiment timing, unit-correct evaluation charts, and
visible invalid-report diagnostics.

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

## Production Persistence (Build-plan Phase 16)

- [x] PostgreSQL source metadata and lifecycle repository
- [x] PostgreSQL operation, checkpoint, and active-generation authority
- [x] Azure Blob original, normalized-document, manifest, and BM25 storage
- [x] Pinecone generations coordinated by PostgreSQL activation state
- [x] Neo4j native entity and relationship generations
- [x] Alembic metadata schema migration
- [x] Explicit resumable inventory, migrate, verify, and cutover CLI
- [x] Production fail-closed backend validation
- [x] Local development and rollback adapters retained
- [x] Restart and incomplete-publication regression tests

**Implementation status:** Complete

**Exit criteria:** Locally tested; live stack verification pending — durable source lifecycle state, migration checkpoints, and active
retrieval/graph generation pointers are authoritative in PostgreSQL. Azure Blob, Pinecone, and
Neo4j artifacts are immutable and become visible only after all required stores verify and the
coordinator atomically publishes the generation. A file-backed SQL restart test recreates the
repository and confirms source and active-generation recovery, while coordinator tests prove
partially prepared generations remain invisible. The production environment rejects every local
backend; local data stays untouched for development and rollback. Live cloud provisioning and
service-level smoke tests belong to Phase 17.

## 18. Observability
- [ ] Trace IDs
- [x] Structured logs
- [ ] Retriever timings
- [x] Fusion trace
- [x] Re-ranker trace
- [ ] LLM token usage
- [ ] User feedback capture

## 19. Security
- [~] JWT/OIDC integration (Phase 18: self-registration, Argon2 password hashing, JWT access
  tokens, and one-use rotating refresh tokens implemented in `app/auth/`; gated behind
  `AUTH_ENABLED`; no external OIDC provider; not verified against live PostgreSQL)
- [~] Workspace/tenant model (workspace-scoped records implemented; Phase 18 adds one private
  workspace per registered client with `app/auth/authorization.py` ownership checks; authenticated
  tenant binding still off by default)
- [~] Source authorization (Phase 18 request-time workspace-ownership enforcement for source
  list/query/inspect/download; security-label authorization deferred)
- [~] Retrieval metadata filters (workspace/source filtering implemented; security-label authorization deferred)
- [x] File validation
- [x] Crawl allowlist
- [~] Secret management (environment-backed references implemented; Phase 18 adds `SecretStr`
  config for Blob, JWT, workflow, Voyage, and mail-webhook secrets with fail-closed validation;
  managed vault deferred)
- [x] SQL read-only enforcement
- [ ] Prompt injection mitigation
- [x] Email verification / password recovery (single-use, one-hour tokens via transactional mail
  webhook; required before public registration is allowed)

## 20. Local Deployment
- [ ] Dockerfile
- [ ] Docker Compose
- [ ] Local PostgreSQL
- [ ] Local Neo4j option
- [x] Health checks

## 21. Azure Deployment

**Status:** [-] Phase 17 skipped by user on September 7, 2026. Tasks remain unimplemented.

- [ ] Azure subscription/resource group
- [ ] Azure Container Apps or App Service
- [ ] Blob Storage
- [ ] Key Vault
- [ ] PostgreSQL
- [ ] Environment configuration
- [ ] Logging/monitoring
- [ ] Deployment workflow

## 22. Vercel Frontend — Optional (Build-plan Phase 18)

**Status:** [~] In progress. Architecture approved September 7, 2026 (`context/design/phase18.md`);
runbook in `docs/phase18-deployment.md`. Code review + fixes on branch `fix/phase18-review`
(uncommitted); prior Phase 18 implementation is uncommitted on `main`.

- [x] Approved architecture and deployment runbook
- [x] Next.js UI (`frontend/`, Next 16 / React 19; `components/workspace.tsx` preserves Chat,
  Sources, Retrieval Lab, Evaluation, Settings, and inspectable citations)
- [x] API client and same-origin proxy (`frontend/lib/`, `frontend/app/api/backend/[...path]`)
  forwarding user JWTs; direct Blob upload/download routes bypassing the 4.5 MB function limit
- [x] Authentication (`app/auth/`: register/login/refresh/logout/me/verify/reset; origin checks;
  refresh cookie scoped to `/api/auth`)
- [x] Per-client workspace isolation and source-ownership authorization (`app/auth/authorization.py`)
- [x] Private Vercel Blob adapter (`app/storage/vercel_blob.py`) for source originals, normalized
  documents, manifests, and BM25 artifacts; `SOURCE_STORAGE_BACKEND` / `LEXICAL_STORE_BACKEND`
  gain a `vercel_blob` option
- [x] Hosted models (`app/hosted/voyage.py`: `voyage-4` embeddings + `rerank-2.5`); new
  1,024-dim cosine Pinecone index; BGE index retained for rollback
- [x] Durable jobs (`app/jobs/`: monotonic step-fence checkpoints with two short transactions per
  step, CSV/PDF/website processing, graph extraction, internal step routes) driven by Vercel
  Workflow (`frontend/workflows/ingest.ts`, `frontend/app/api/workflows/start`)
- [x] Serverless config profile and fail-closed validation (`SERVERLESS`, `AUTH_ENABLED`,
  `EMBEDDING_DIMENSION=1024`, Voyage/Blob/workflow/mail secrets) in `app/core/config.py`;
  `vercel_blob` in production also requires `AUTH_ENABLED`
- [x] Split dependency groups: runtime deps exclude FAISS/torch/transformers/streamlit (moved to
  the `local` group); `requirements.txt` exported for Vercel; `vercel.json` / `[tool.vercel]`
- [x] Migration `20260907_0002` is an explicit self-contained `op.create_table` snapshot
- [x] Offline test coverage green: `test_phase18_auth.py` (incl. nested-`workspace_id` rejection),
  `test_phase18_hosted.py`, and `test_phase18_jobs.py` (CSV/PDF parametrized retry/publish path)
- [ ] Deploy to Vercel (two projects) and run the acceptance checklist
- [ ] Live provider verification: Voyage quality/threshold calibration, Workflow dispatch,
  concurrent job-step delivery on PostgreSQL, private Blob transfer, mail delivery
- [ ] Connect to hosted backend end-to-end (build plan assumed Azure; resolved as Vercel-hosted API)

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
source-specific PDF page, website URL, and CSV row citations. Advanced inline rendering and
evidence inspection were subsequently completed in Phase 13.

### Milestone B — Advanced Retrieval
Sentence windows + graph + BM25 + fusion + re-ranking.

Status: [x]

Completed through Phases 6–10 with independently selectable sentence-window, graph, and lexical
retrieval, deterministic fusion, and optional cross-encoder re-ranking backed by focused
benchmarks.

### Milestone C — Enterprise UX
Streamlit source manager + chat + evidence inspector + evaluation.

Status: [x]

Completed through Phases 13–15 with inspectable claim-level citations, source management, routed
chat, retrieval diagnostics, and immutable evaluation-report visualization.

### Milestone D — Production Deployment
Azure persistence and deployment, optionally Vercel frontend.

Status: [~]

Phase 16 durable persistence is implemented (PostgreSQL + Blob + Pinecone + Neo4j) with local
regression coverage. Phase 17 Azure deployment is skipped by user instruction. Phase 18 pivots
deployment to Vercel (Next.js frontend + FastAPI API, private Vercel Blob, hosted Voyage models,
self-registration auth, Workflow-orchestrated durable jobs); the code exists but is uncommitted,
has failing durable-job tests, and has not been deployed or verified against live cloud services.

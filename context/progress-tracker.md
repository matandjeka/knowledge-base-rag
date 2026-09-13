# Advanced Multi-Source Enterprise RAG — Progress Tracker

## PostgreSQL graph storage update — September 12, 2026

Plain PostgreSQL graph snapshots replace Neo4j in the current deployment plan. The new adapter,
`20260912_0005` migration, dependency wiring, settings response, local launcher, and deployment
configuration checker support this path. Neo4j remains available for existing deployments;
existing graphs require an explicit rebuild/migration before switching. Historical phase notes
below describe the original Neo4j implementation. Hosted PostgreSQL, Voyage, mail configuration,
and cloud acceptance are still outstanding. Graph traversal remains in application memory.

Validation: 262 backend tests passed, 1 opt-in live-model test skipped; Ruff lint/format and
strict typing passed (113 app files); frontend typecheck and 3 tests passed. Migration
`20260912_0005` was applied to local PostgreSQL. A real PostgreSQL smoke test in a temporary
schema passed concurrent prepare retries, immutable-write rejection, publication visibility,
reconnect recovery, and workspace isolation; the temporary schema was removed afterward.

## OpenAI embedding update — September 12, 2026

The current deployment profile and authenticated local launcher now use OpenAI
`text-embedding-3-small` at 1,024 dimensions and `RERANKER_PROVIDER=none`. Fusion remains
available; dedicated reranking is explicitly unavailable in the UI/API. Voyage is a legacy
optional adapter. Existing vector indexes require re-embedding into the new model space;
live relevance calibration and full cloud acceptance remain outstanding. Historical Voyage
and Phase 10 notes below describe earlier implementation, not the current deployment profile.

Validation: 274 backend tests passed, 1 opt-in live graph test skipped; lint/format and strict
app typing passed (114 files). Frontend typecheck, all 3 frontend tests, and the production
build passed. A live synthetic-text OpenAI request returned 1,024-dimensional embeddings.
The provider tests cover batching/order, malformed vectors, model identity, transient retries,
sanitized errors, missing-key/dimension validation, disabled reranking, serverless configuration,
and rejection of old-model job checkpoints. The local `.env` and launcher now select OpenAI;
existing sources were not automatically re-embedded. Live retrieval-quality acceptance remains
pending, and the application has not been deployed to Vercel.

## Overall Status
**Last Reviewed:** September 12, 2026

**Last Completed Implementation Phase:** Phase 19 — Enterprise Hardening (implementation-complete
and code-reviewed; not deployment-validated).

**Current Phase:** None in active development. Every build-plan phase is either implemented or
explicitly skipped. Remaining work includes local UI acceptance, live retrieval-quality evaluation, cloud
deployment / restart-recovery acceptance for Phases 16, 18, and 19, and the implementation
follow-ups listed below (see **Open Validation Work and Follow-ups**).

**Skipped Phase:** Phase 17 — Azure Deployment, per user instruction on September 7, 2026.
No Azure deployment implementation or provisioning was performed.

**Exit-Criteria Verification:** Automated local coverage exists through Phase 19. Live model
quality, full UI ingestion/query reliability, full-stack production restart recovery, and cloud
Vercel/Voyage/Blob/Workflow/PostgreSQL/Neo4j acceptance remain unverified; local PostgreSQL-backed
API startup and Next.js HTTP availability have been verified; Phases 16, 18, and 19
are implementation-complete, not deployment-validated.

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
Phase 17 Azure Deployment is skipped. Phase 18 (Optional Vercel Frontend) is implemented and
merged to `main` (PR #16): two Vercel projects (FastAPI API + Next.js frontend) with Vercel Blob,
hosted Voyage embeddings/reranking, a new 1,024-dim Pinecone index, self-registration auth, and
Vercel Workflow-orchestrated durable jobs — never live-deployed. Phase 19 (Enterprise Hardening)
is implemented, code-reviewed, and merged to `main` (PR #17): 19a identity & access
(organizations, memberships, roles `viewer<member<admin<owner`, invitations, centrally
role-gated endpoints), 19b governance (hash-chained audit log with prune-aware `verify_chain`,
retention policies + `rag-retention` purge CLI, source security labels with clearance-filtered
retrieval), and 19c abuse & content safety (PII-redacting log filter, per-caller rate limiting,
non-blocking prompt-injection ingestion scan on both the sync and durable-jobs paths + LLM-caller
delimiting, per-workspace crawl domain allowlist). Not deployed/live-verified; the governance
admin UI and session-reuse audit emission are tracked follow-ups. Design and threat model:
`context/design/phase19.md`.
Tracker sections are grouped by subsystem, so their section numbers do not map one-to-one to the
phase numbers in `build-plan.md`. Production mode now requires the complete durable stack while
the existing local adapters remain available for development, tests, and rollback.

**Current verification (September 12, 2026, current working tree):** `ruff check .` passes;
`ruff format --check .` reports 165 files already formatted; `mypy app` passes across 112 source
files; `pytest -q` reports **260 passed, 1 skipped** in 37.22 seconds (opt-in live OpenAI graph
test skipped); `alembic heads` reports the single head `20260908_0004`. Frontend
`npm run typecheck --prefix frontend` and `npm test --prefix frontend` pass (3 tests).
The local authenticated API and Next.js frontend were restarted successfully: frontend HTTP 200
and API `/health` returned `{"status":"ok"}`. The launcher applied migrations against embedded
PostgreSQL. This checks startup, not login, upload, Workflow execution, indexed-data recovery,
or a production frontend build. Existing uncommitted implementation/demo changes are included
in this working-tree verification; it is not a new merged-release claim.

**Historical verification (September 9, 2026, on `main`; retained from prior review):** `ruff check .` passes; `ruff format --check .`
reports 162 files already formatted; strict `mypy app` passes across 112 source files; `pytest -q`
reports **`258 passed, 1 skipped`** in ~29 seconds (the skipped test is the opt-in live OpenAI
graph integration test); `alembic heads` is a single head `20260908_0004`; frontend
`npm run typecheck` and `node --test` (3 tests) pass. The `demo/` walkthrough
(`scripts/seed_demo.py`) has been run end to end more than once, including from the Streamlit
Chat tab against the live API: all six sources reach `ready` and PDF / website / CSV questions
return grounded, correctly cited answers (graph + SQL need `OPENAI_API_KEY`).
Two opt-in config flags were added for the demo and for internal-network use: `SQL_ALLOW_SQLITE`
and `WEBSITE_ALLOWED_PRIVATE_HOSTS` (both empty/off by default). Phase 19 added
`tests/test_phase19_orgs.py` (org/role/invitation/isolation + HTTP audit/retention/crawl-allowlist
+ login-audit), `tests/test_phase19_governance.py` (hash-chain tamper + prefix-prune, retention
cascade, clearance filtering), and `tests/test_phase19_content_safety.py` (redaction, rate limiter,
injection markers, domain allowlist). These checks still do not exercise any live
Vercel/Voyage/Blob/Workflow/PostgreSQL/Neo4j path, nor live embedding/re-ranking quality.

## Open Validation Work and Follow-ups

These items distinguish local UI checks, live service/model acceptance, and unfinished
implementation. Passing offline tests or local health checks does not complete cloud acceptance.

- [~] **Streamlit runtime acceptance** — the demo dataset (2 PDFs, a crawled website, 2 CSVs, a
  SQLite DB) has been ingested end to end via the API and queried from the Streamlit **Chat** tab
  with correct citations. Still to do through the **UI itself**: the PDF / website / CSV upload
  forms and the source inspector / retrieval-trace panels (build-plan Phase 15). The
  exit-code-139 embedding crash is resolved by `OMP_NUM_THREADS=1`.
- [ ] **Retrieval quality (Phase 10)** — verify re-ranking improvement over fused-only results
  with real models; deterministic fixtures validate contracts and metrics, not live quality.
- [ ] **Phase 16 restart recovery** — PostgreSQL + Azure/Vercel Blob + Pinecone + Neo4j together,
  including indexed-data retrieval after a restart. Graph snapshots now have a real local
  PostgreSQL reconnect smoke test; full-stack acceptance remains pending. Earlier persistence tests use SQLite, in-memory
  coordination, and simulated Blob.
- [ ] **Phase 18 Vercel stack** — provision two Vercel projects, private Blob, PostgreSQL `rag`
  schema (including graph snapshots), a new 1,024-dim Voyage Pinecone index, Voyage key, and a mail webhook, then run
  the `docs/phase18-deployment.md` acceptance checklist (client isolation, session/refresh
  rotation, direct Blob uploads >4.5 MB, retry idempotency, interrupted-job recovery, retrieval
  after restart/redeploy). Also verify Workflow dispatch, concurrent job-step delivery against
  PostgreSQL, and transactional mail delivery.
- [ ] **Voyage calibration** — live embedding/reranking quality comparison; calibrate similarity
  thresholds (the BGE 0.70 cutoff is not a validated Voyage value); reindex against the new index.
- [ ] **Phase 19 governance runtime** — with `AUTH_ENABLED` on PostgreSQL: exercise the
  invitation → acceptance email flow, `rag-audit verify` and `rag-retention apply` against real
  data (including an audit prefix-purge and a subsequent `verify` showing `pruned_through`),
  clearance-filtered retrieval with two members at different clearances, and rate-limit behaviour
  under concurrency.
- [ ] **Additional implementation follow-ups** — token-budget management, provider token/cost
  accounting, trace IDs, and user feedback remain unfinished in the subsystem checklists; these
  are tracker extensions rather than explicit tasks in `build-plan.md`.
- [ ] **Phase 19 follow-ups** — build the governance admin UI in the Next.js app (orgs / members /
  audit / retention / labels / crawl allowlist); emit an audit event on refresh-token-reuse
  detection.
- [-] **Deferred with skipped Phase 17** — Docker/deployment artifacts, Azure resources, Key Vault,
  monitoring, and an end-to-end deployment smoke test. `docker/` remains a placeholder; `scripts/` now contains working local launch, demo, and configuration-check tools.

**Recently closed:** the local embedding crash (Sept 7); the Phase 18 review fixes and runtime
500→503/401 fixes (Sept 7); the two Phase 19 code-review rounds — org-model isolation gaps, the
audit `verify_chain`-vs-retention-purge contradiction, the durable-jobs content-safety gap, the
PostgreSQL-unsafe `record_query`, and over-broad log redaction (Sept 8); the `demo/` dataset,
`scripts/seed_demo.py`, `demo/GUIDE.md`, and the `SQL_ALLOW_SQLITE` / `WEBSITE_ALLOWED_PRIVATE_HOSTS`
opt-in flags they need (Sept 8).

**Housekeeping (Sept 9):** a real OpenAI key had been pasted into the working-tree copy of
`.env.example` (never committed — `git log -S` finds it nowhere in history); reverted to the
committed placeholder. The key still lives in the local `.env` (git-ignored). If that file was
ever shared or pushed from another checkout, rotate the key at platform.openai.com.

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
| Phase 10 — Re-ranking | [x] Implemented; real-model relevance improvement pending |
| Phase 11 — Structured Database Retrieval | [x] Complete |
| Phase 12 — Query Router | [x] Complete |
| Phase 13 — Advanced Citation Engine | [x] Complete |
| Phase 14 — Evaluation Framework | [x] Complete |
| Phase 15 — Streamlit Application | [x] Implemented; remaining UI-form and inspection acceptance pending |
| Phase 16 — Production Persistence | [x] Implemented; live stack restart verification pending |
| Phase 17 — Azure Deployment | [-] Skipped by user; not implemented |
| Phase 18 — Optional Vercel Frontend | [x] Implemented and merged to `main` (PR #16): Next.js frontend, same-origin JWT proxy, self-registration auth, private Vercel Blob adapter, hosted Voyage models, durable Workflow jobs. No live Vercel deployment or cloud verification. |
| Phase 19 — Enterprise Hardening | [x] Implemented, code-reviewed (two rounds), and merged to `main` (PR #17): 19a identity & access, 19b governance, 19c abuse/content safety. Current working-tree offline suite green (260 passed, 1 skipped). Not deployed/live-verified; governance admin UI + session-reuse audit emission are tracked follow-ups. |

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

**Exit criteria:** Deterministic benchmark coverage complete; real-model improvement pending — fused candidates can be re-ranked through a lazy Hugging Face
cross-encoder, selected with a deterministic two-pass source-diversity policy, and returned with
stable citations plus inspectable fusion, model-score, rank, and latency provenance. A committed
eight-case graded benchmark with deterministic fixtures improves MRR and NDCG over fused-only
ordering; it does not establish improvement with real models.

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

**Exit criteria:** Implementation and automated coverage complete; UI runtime acceptance partial —
the native Streamlit multipage shell exposes Chat, Sources,
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
backend; local data stays untouched for development and rollback. Azure deployment remains skipped with Phase 17; live cloud provisioning and
service-level restart/retrieval checks remain required under the Phase 18 deployment track.

## 18. Observability
- [ ] Trace IDs
- [x] Structured logs (Phase 19c adds a PII/secret redaction filter on the root handler)
- [x] Audit trail (Phase 19b — hash-chained `rag_audit_events`, `GET /audit/events|verify`)
- [ ] Retriever timings
- [x] Fusion trace
- [x] Re-ranker trace
- [ ] LLM token usage
- [ ] User feedback capture

## 19. Security
- [~] JWT/OIDC integration (Phase 18: self-registration, Argon2 password hashing, JWT access
  tokens, and one-use rotating refresh tokens implemented in `app/auth/`; gated behind
  `AUTH_ENABLED`; no external OIDC provider; not verified against live PostgreSQL)
- [x] Organization / RBAC model (Phase 19a, merged to `main` PR #17): `rag_organizations`
  + `rag_memberships` + `rag_invitations`; an organization owns one workspace; registration
  auto-creates a personal org (`owner`); roles `viewer` < `member` < `admin` < `owner` gate
  every mutation centrally in `authorize()`; `/orgs` router manages members and invitations;
  migration `20260908_0003` backfills personal orgs; `/auth/me` self-heals and lists orgs.
  Not yet deployed/live-verified; governance admin UI is a Next.js follow-up.
- [x] Source authorization (Phase 18 request-time workspace-ownership; Phase 19a: membership +
  role check with a single-workspace-per-request rule; Phase 19b: `Classification` label on each
  source + per-membership `clearance` gate `GET /sources`, inspection, and retrieval)
- [x] Retrieval metadata filters (workspace/source filtering; Phase 19b threads a clearance-derived
  visible-source-id set through every retriever and the auto router so above-clearance content
  never enters the candidate set)
- [x] Audit logging (Phase 19b — `app/audit/`): append-only `rag_audit_events` with a per-org
  hash chain; `verify_chain` anchors to the first surviving event so a retention prefix-purge
  reports `pruned_through` instead of a false break; `record()` emit points across org,
  membership, governance, source-classification, and (19c) `auth.login`/`auth.login_failed`
  actions; `GET /audit/events|verify` (admin+); `rag-audit verify` CLI. Source-registration and
  refresh-reuse emission are follow-ups.
- [x] Retention policies (Phase 19b — `app/retention/`): per-workspace day windows for source
  data / audit events / query counters (default keep-forever), `rag-retention apply [--dry-run]`
  cascading purge with a 30-day audit floor and a `retention.purged` audit trail, `GET/PUT
  /retention` (admin+). Vector/graph generation GC deferred (immutable-generation design).
- [x] File validation
- [~] Secret management (environment-backed references implemented; Phase 18 adds `SecretStr`
  config for Blob, JWT, workflow, Voyage, and mail-webhook secrets with fail-closed validation;
  managed vault deferred)
- [x] SQL read-only enforcement
- [x] Prompt injection mitigation (Phase 19c): non-blocking `app/ingestion/injection_scan.py`
  heuristic marks suspicious sources (`injection_flags`) on both the sync and durable-jobs
  ingestion paths; OpenAI graph-extraction and NL→SQL callers delimiter-wrap untrusted content,
  and structured-output + substring / AST validation reject non-grounded output. No guard LLM.
- [x] Rate limiting (Phase 19c — `app/core/rate_limit.py`): fixed-window per-user / per-IP
  counters (`rag_rate_limits`, in-memory fallback) on `/query`, ingestion, and `POST /jobs`;
  `RATE_LIMIT_*` settings; 429 + `Retry-After`.
- [x] PII-aware logging (Phase 19c — `app/core/redaction.py`): root-handler `logging.Filter`
  masks bearer tokens, key/value secrets, e-mails, and long opaque strings in the message and
  non-allowlisted extras; UUIDs and hex ids/hashes are exempted so operational logs stay useful.
- [x] Website allowlist (Phase 19c): per-workspace `rag_retention_policies.crawl_allowlist`,
  `GET/PUT /crawl-allowlist` (admin+), enforced on the seed host with subdomain matching on both
  the sync and durable-jobs crawl paths; the always-on SSRF / non-global-IP guard remains beneath it.
- [x] Email verification / password recovery (single-use, one-hour tokens via transactional mail
  webhook; required before public registration is allowed)

## 20. Local Deployment
- [ ] Dockerfile
- [ ] Docker Compose
- [x] Local PostgreSQL — `scripts/local_auth_stack.py` starts persistent embedded PostgreSQL
  in `.localdb/`, applies Alembic migrations, and launches the authenticated API; startup and
  restart smoke-verified September 12, 2026.
- [x] Local authenticated Next.js/API launch — frontend HTTP 200 on port 3000 and API
  `/health` returns `{"status":"ok"}` on port 8000 after restart. Email verification is disabled
  by the local launcher; account flows and indexed-data recovery were not tested in this check.
- [x] Local FAISS durable-job adapter — embedding checkpoints are assembled into baseline and
  sentence-window generations at publish time (`app/jobs/processing.py`); automated coverage
  in `tests/test_phase18_jobs.py`. Live Blob/Workflow upload acceptance remains pending.
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

**Status:** [~] Implemented and merged to `main` (PR #16), code-reviewed; **not deployed**.
Architecture: `context/design/phase18.md`; runbook: `docs/phase18-deployment.md`. The checklist
below is complete for implementation; the remaining `[ ]` items all require a live Vercel stack.

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
- [x] Demo dataset — `demo/` (fictional "Meridian Robotics": 2 PDFs, a crawled internal website,
  2 CSVs, a read-only SQLite database) authored to exercise every retrieval strategy
- [x] Demo script — `demo/GUIDE.md` (walkthrough + per-strategy question bank) and
  `scripts/seed_demo.py` (one command: build assets, serve the site, launch the API, ingest all
  six sources). Verified end-to-end locally; graph + SQL retrieval need `OPENAI_API_KEY`.
- [ ] Screenshot set
- [ ] Recorded walkthrough
- [x] README (includes a "Try the demo" section)
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
regression coverage. Phase 17 Azure deployment is skipped by user instruction. Phase 18 (Vercel:
Next.js frontend + FastAPI API, private Vercel Blob, hosted Voyage models, self-registration auth,
Workflow-orchestrated durable jobs) and Phase 19 (enterprise hardening: orgs/RBAC, hash-chained
audit, retention, security labels, rate limiting, injection scan, crawl allowlist, PII-redacting
logs) are implemented, code-reviewed, and merged to `main` with the full offline suite green.
The milestone stays `[~]` because no live cloud stack has been provisioned and none of the
deployment / restart-recovery acceptance checks have been run — see **Open Validation Work and Follow-ups**.

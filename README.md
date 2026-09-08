# Enterprise Knowledge Fusion RAG

A modular, multi-source retrieval-augmented generation platform for grounded answers with
inspectable citations.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
cp .env.example .env
```

Run the API:

```bash
uv run uvicorn app.main:app --reload
```

Run the Streamlit UI in another terminal:

```bash
uv run streamlit run ui/streamlit_app.py
```

Run quality checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

The API health endpoint is available at `http://127.0.0.1:8000/health`.

## Production persistence

Local development continues to use the in-memory source registry, filesystem artifacts, FAISS,
and local graph generations. Production mode fails closed unless all durable adapters are
configured: PostgreSQL for source metadata, lifecycle state, operation checkpoints, and active
generation pointers; Azure Blob Storage for originals, normalized documents, crawl manifests,
and immutable BM25 generations; Pinecone for vectors; and Neo4j for graph generations.

Configure the `METADATA_*`, `AZURE_*`, `PINECONE_*`, and `NEO4J_*` variables documented in
`.env.example`, then create the PostgreSQL schema before starting the application:

```bash
uv run alembic -x schema=rag upgrade head
```

Azure managed identity through `DefaultAzureCredential` is preferred. A storage connection string
is supported for controlled migration and local integration environments. Secrets are accepted
only through environment-backed settings and are omitted from effective-settings responses.

Indexing stages immutable artifacts in every required store, verifies them, and publishes a single
PostgreSQL generation pointer last. Readers therefore never see a partially written generation,
and another application replica observes the same active state after restart.

### Local-to-production migration

The migration is explicit and resumable. It preserves source and generation UUIDs, records
checksum checkpoints in PostgreSQL, and never deletes local data:

```bash
uv run rag-migrate-persistence inventory
uv run rag-migrate-persistence migrate --dry-run
uv run rag-migrate-persistence migrate
uv run rag-migrate-persistence verify
uv run rag-migrate-persistence cutover
```

Use `--workspace WORKSPACE_ID` to constrain a run and `--migration-id UUID` to resume a named
migration. If verification or cutover fails, keep the application on local backend settings,
correct the reported artifact, and rerun with the same migration ID. Rollback after cutover is a
configuration rollback to the untouched local stores; remote generation cleanup is deliberately
manual so a failed rollout cannot destroy the last known-good data.

## Web interface

The Streamlit application provides five workflows through native multipage navigation:

- Chat uses automatic routing across selected ready sources.
- Sources registers, lists, inspects, and re-indexes PDF, website, CSV, and PostgreSQL sources.
- Retrieval Lab runs session-scoped explicit retrieval experiments.
- Evaluation displays immutable benchmark reports and deployment gates.
- Settings shows secret-safe effective backend configuration and session UI preferences.

Evaluation reports are read from `data/evaluations` by default. Point
`EVALUATION_REPORTS_DIR` at the output directory passed to `rag-evaluate` when reports are stored
elsewhere. The MVP interface deliberately uses the fixed `local` workspace.

## Vector stores

FAISS is the default local backend and requires no additional configuration.

To use an existing Pinecone index, provision it with 384 dimensions and cosine similarity,
then configure:

```dotenv
VECTOR_STORE_BACKEND=pinecone
PINECONE_API_KEY=your-api-key
PINECONE_INDEX_NAME=your-index-name
PINECONE_INDEX_HOST=your-index-host.svc.pinecone.io
```

The application keeps each workspace in a separate Pinecone namespace. It stages complete
generations before activation, applies source filters in Pinecone, and performs stale-generation
cleanup after a successful activation. It never creates or deletes the configured Pinecone index.

## Retrieval modes

Every ingestion rebuilds three representations under one coordinated workspace generation:

- `vector` searches the existing normalized chunks and remains the default.
- `sentence_window` searches individual sentences, then returns the matched sentence with its
  neighboring context.
- `lexical` uses a durable local BM25 index for exact identifiers, acronyms, policy numbers, and
  uncommon terminology.

Select sentence-window retrieval per request:

```json
{
  "workspace_id": "example-workspace",
  "question": "What surrounds the retention requirement?",
  "retrieval_mode": "sentence_window"
}
```

`SENTENCE_WINDOW_RADIUS` controls how many sentences are included on either side of a match and
defaults to `2`. Changing it requires rebuilding the source index. Index generations created
before sentence-window support must also be rebuilt; the API reports a clear missing-index error
until then. Windows never cross a normalized document boundary, so source citation locators remain
unchanged.

Select lexical retrieval explicitly when exact terminology matters:

```json
{
  "workspace_id": "example-workspace",
  "question": "What does policy HR-402 require?",
  "retrieval_mode": "lexical"
}
```

`BM25_K1` and `BM25_B` configure BM25 saturation and length normalization.
`LEXICAL_TITLE_BOOST` gives matching title terms a modest additional weight, and
`LEXICAL_MIN_SCORE` controls the independent raw-score floor. BM25 raw scores are not comparable
to vector similarities; fusion therefore combines ranks rather than these raw scores.

## Fusion retrieval

Fusion queries concurrently retrieve a larger candidate pool, collapse results that represent the
same underlying passage, and return one deterministic list. Standard reciprocal rank fusion is the
default, so incomparable vector, graph, and BM25 raw scores are never mixed directly.

```json
{
  "workspace_id": "example-workspace",
  "question": "What does policy HR-402 require?",
  "retrieval_mode": "fusion",
  "fusion_retrievers": ["vector", "sentence_window", "lexical"],
  "fusion_strategy": "rrf"
}
```

Omitting `fusion_retrievers` uses vector, sentence-window, and lexical retrieval. Graph must be
requested explicitly because its workspace index is optional. At least two distinct retrievers are
required. If any requested retriever or index fails, the fused query fails rather than silently
changing strategy.

Set `fusion_strategy` to `weighted_rrf` to apply the configured retriever weights. Active weights
are normalized before use. `FUSION_RRF_K` controls the rank constant and
`FUSION_CANDIDATE_MULTIPLIER` controls the per-retriever candidate depth, capped by
`RETRIEVAL_MAX_TOP_K`. Each fused evidence item exposes its original ranks, effective weights, and
score contributions in `metadata.fusion_contributions`.

## Cross-encoder re-ranking

Re-ranking is an opt-in stage after fusion. It scores a larger fused candidate pool with a lazy
Hugging Face cross-encoder, applies a deterministic per-source diversity pass, and returns the
requested final `top_k` evidence items:

```json
{
  "workspace_id": "example-workspace",
  "question": "What does policy HR-402 require?",
  "top_k": 5,
  "retrieval_mode": "fusion",
  "fusion_retrievers": ["vector", "sentence_window", "lexical"],
  "rerank": true
}
```

`RERANKER_MODEL_NAME` defaults to `BAAI/bge-reranker-base`. Batch size, maximum sequence length,
device, candidate-pool size, and the first-pass per-source cap are configurable through the
corresponding `RERANKER_*` and `RERANKING_*` settings. The first re-ranked query may download and
load the configured model. Model or inference failures return an explicit service error; the
application does not silently fall back to fused-only ordering.

Re-ranked evidence preserves its stable fused identity and citation metadata. Its `raw_score` is
the cross-encoder score, while fusion rank, fusion score, final rank, model identity, and measured
latency are available under `metadata.reranking`.

## Knowledge graph retrieval

Graph indexing is optional and explicit. Configure an OpenAI model that supports structured
outputs:

```dotenv
OPENAI_API_KEY=your-api-key
GRAPH_EXTRACTION_MODEL=your-model-name
```

Graph extraction sends the normalized text of every ready source in the selected workspace to
the configured OpenAI API. Confirm that this data flow complies with your organization’s privacy,
residency, retention, and access-control requirements before enabling graph indexing. Credentials
remain environment-only and are never written into graph artifacts.

Then rebuild the complete graph for a workspace:

```http
POST /graph/index
Content-Type: application/json

{"workspace_id":"example-workspace"}
```

The rebuild reads every persisted ready source, extracts a controlled entity/relationship
ontology, and atomically activates a checksummed local graph generation. Vector ingestion and
queries do not require graph credentials.

Select deterministic two-hop graph traversal per query:

```json
{
  "workspace_id": "example-workspace",
  "question": "Which policy governs Project Atlas?",
  "retrieval_mode": "graph"
}
```

Graph questions must name a known entity or alias. Automatic graph routing and LLM-based query
planning remain deferred. Multi-source paths produce edge-level citations for every supporting
PDF page, website URL, or CSV row.

The normal test suite uses offline extractor doubles. A live extraction smoke test is available
only when explicitly enabled with `RUN_LIVE_OPENAI_GRAPH_TEST=1` and valid graph settings.

## Phase 18 — Next.js and Vercel

The Next.js client lives in `frontend/`. It supports client registration, private workspaces,
source ingestion, cited chat, retrieval experiments, evaluation reports, and settings.

```sh
npm ci --prefix frontend
npm run dev --prefix frontend
```

Unlike the local Streamlit interface, this client requires the authenticated API configuration.
Deployment uses PostgreSQL, private Vercel Blob, Pinecone, Neo4j, Voyage models, and Vercel Workflow.
See [the deployment runbook](docs/phase18-deployment.md) for configuration, migrations,
index migration, verification, and rollback. Azure deployment (Phase 17) remains skipped.

`uv sync` retains local ML dependencies for development. The production dependency set
(`uv sync --no-dev`) excludes FAISS, Sentence Transformers, PyTorch, and Streamlit.
`requirements.txt` is the pinned production export of `uv.lock`.

Phase 18 deployment acceptance remains pending until service credentials, email delivery,
and live preview smoke tests are complete. Run `uv run python scripts/check_phase18_config.py`
to list missing configuration names without displaying secrets.

## Phase 19 — Enterprise hardening

Ships as three sub-milestones (`context/design/phase19.md`). **19a — identity & access** is
implemented: an organization owns one workspace, users join with a role
(`viewer` < `member` < `admin` < `owner`), and every mutating endpoint is role-gated in
`app/auth/authorization.py`. Registration auto-creates a personal organization, so the existing
single-user flow is unchanged. Manage members and invitations under `/orgs` (admin+); run
`alembic upgrade head` to apply `20260908_0003`, which backfills a personal organization for
every existing account. Invitations reuse `AUTH_EMAIL_WEBHOOK_*` with a new `invite` template.
Organization features require `AUTH_ENABLED`; local Streamlit mode stays single-tenant.

### Phase 19b — governance

Hash-chained audit log (`app/audit/`, `GET /audit/events` + `rag-audit verify`), per-workspace
retention policies (`GET/PUT /retention`, `rag-retention apply [--dry-run]` with a 30-day audit
floor), and source security labels: a `Classification` (`public` < `internal` < `confidential` <
`restricted`) on each source and a per-member `clearance` that filters `GET /sources`, source
inspection, and every retriever's candidate set. Apply `20260908_0004` with `alembic upgrade head`.

### Phase 19c — abuse & content safety

Applies in local and authenticated mode. A root-handler log filter (`app/core/redaction.py`)
masks e-mails, bearer tokens, and secrets. Fixed-window rate limiting (`RATE_LIMIT_*`) guards
`/query`, ingestion, and `POST /jobs` (429 + `Retry-After`). A non-blocking heuristic scan
(`app/ingestion/injection_scan.py`) marks sources containing prompt-injection patterns
(`config.options.injection_flags`), and the OpenAI graph/SQL callers delimiter-wrap untrusted
content. `GET/PUT /crawl-allowlist` (admin+) sets a per-workspace list of crawlable domains.

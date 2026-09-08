# Phase 18 — Vercel deployment runbook

## Architecture

Two Vercel projects share this repository: API root `.` (FastAPI), frontend root `frontend/`
(Next.js). A same-origin frontend proxy forwards user JWTs to the API. Python validates sessions
and workspace ownership independently. The proxy does not expose workflow endpoints. Direct
Blob uploads bypass function request-body limits; download URLs expire after 60 seconds.

Vercel Workflow runs in the frontend project, calling bounded Python job steps with a separate
server-only credential. Each step holds a database row lock, advances a monotonic checkpoint,
and completes within a 220-second application deadline. Configure 300-second function budgets.
A persistent workspace reservation serializes index writers. Never use in-process background
tasks as a substitute for Workflow.

## Required services and configuration

Use `.env.example` and `frontend/.env.example` for variable names. No secret belongs in a
`NEXT_PUBLIC_` variable or the repository.

- Vercel private Blob store connected to both projects; server-only read/write token.
- PostgreSQL metadata database with a dedicated `rag` schema. Use direct/session-compatible
  connections for migrations and transaction-pool-compatible runtime settings. The runtime
  currently sets `search_path` in connection parameters; verify pooler support or use a direct URL.
- New Pinecone **cosine, 1024-dimensional** index for Voyage. Keep the old BGE index intact.
- Neo4j URI/database/credentials as in Phase 16.
- Voyage API key. Default embedding `voyage-4`; initial reranker `rerank-2.5`, pending live evaluation.
- JWT secret and workflow secret: independent random values of at least 32 characters.
- A transactional mail webhook accepting `{to, template, url}` with a bearer secret.
  Templates are `verify` and `reset`; return a successful HTTP status only after accepting delivery.
  Configure this before allowing public registration. Tokens expire in one hour and are single-use.
- Existing OpenAI configuration for optional graph extraction and natural-language SQL.

For API production set `APP_ENV=production`, `SERVERLESS=true`, `AUTH_ENABLED=true`,
`AUTH_REQUIRE_VERIFICATION=true`, metadata `postgresql`, source/lexical `vercel_blob`,
vector `pinecone`, graph `neo4j`, embedding/reranker `voyage`, and `EMBEDDING_DIMENSION=1024`.
Set `FRONTEND_URL`, `AUTH_ALLOWED_ORIGINS`, and `WORKFLOW_DISPATCH_URL` to the deployed frontend.
Frontend `RAG_API_URL` points to the API; `FRONTEND_ORIGIN` matches its own origin.
If preview deployment protection is enabled, configure bypass credentials for both call directions.

## Build and migrations

Local development: `uv sync` includes the local ML/UI dependency group through `dev`.
Production dependencies: `uv sync --no-dev`; FAISS and Sentence Transformers are lazy imports.
`requirements.txt` is exported from the lockfile with:

```sh
uv export --frozen --no-dev --no-hashes --no-emit-project -o requirements.txt
npm ci --prefix frontend
npm run build --prefix frontend
uv run alembic -x schema=rag upgrade head
```

Use Vercel CLI >=48.1.8 (the machine's original 39.x CLI does not support this FastAPI workflow).
Deploy API and frontend previews, set their reciprocal service URLs, then redeploy and verify.
Run migrations once before serving authentication or job traffic. Do not run migrations on every
function cold start. No local source is assigned automatically to a newly registered client.
An operator must explicitly assign ownership before importing any legacy documents.

## Acceptance and cutover

1. Register two clients; verify mail delivery, login, refresh rotation, logout and recovery.
2. Confirm client B cannot list, query, inspect or download client A's sources by changing IDs.
3. Upload a PDF and CSV larger than 4.5 MB directly to Blob; inspect schema and configure columns.
4. Ingest a website. Close the browser; verify the job still finishes. Retry a failed step and
   redeliver a completed step. Check that no partial generation is published.
5. Query vector, window, lexical and fusion modes; verify page/URL/row citations and private originals.
6. Run live Voyage quality comparisons and calibrate similarity thresholds. The BGE 0.70 threshold
   is a local default, not a validated Voyage cutoff. Keep model/dimension constant during a job.
7. Restart/redeploy both projects and repeat retrieval. Verify evaluation reports are stored under
   `workspaces/<workspace_id>/evaluations/<report>.json` in private Blob.
8. Check function bundle size, memory, maximum step duration, provider costs, and logs without source
   text. Promote only after these checks pass.

Jobs are limited to 10,000 combined chunk/window documents by default. Parsing/lexical steps still
load bounded workspace artifacts in memory. Larger corpora require sharded lexical storage and
incremental parsing; exceeding configured limits fails explicitly. A failed dispatch leaves a
persistent job and workspace reservation; use **Resume** to dispatch again or **Cancel** to release it.

Rollback deploys the previous frontend/API revisions together and restores their compatible model
and index settings. Never switch only the embedding provider against an existing incompatible index.
No old index or source data is automatically deleted.

## Verification scope

Offline tests exercise actual password hashing/JWT verification, SQLite-backed session and job
transactions, provider response validation, private Blob transport doubles, PDF/CSV ingestion,
and graph job checkpoints. Browser tests mock API responses; they establish UI behavior, not
live cloud connectivity. PostgreSQL row-lock concurrency, Vercel Workflow delivery, provider
quality, private uploads/downloads, and mail delivery require the configured preview environment.

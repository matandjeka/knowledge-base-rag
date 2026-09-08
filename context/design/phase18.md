# Phase 18 — Approved Vercel architecture

Approved September 7, 2026. Implementation in progress; Phase 17 remains skipped.

- Next.js frontend and FastAPI API in separate Vercel projects.
- Self-registration, JWT access tokens, rotating refresh cookies, one private workspace per client.
- Private Vercel Blob replaces Azure Blob for production source and lexical artifacts.
- PostgreSQL stores accounts, source metadata, checkpoints and publication state.
- Pinecone uses a new 1,024-dimensional Voyage index; retain the previous BGE index for rollback.
- Neo4j remains the graph store. Voyage provides embeddings and reranking.
- Vercel Workflow orchestrates authenticated, bounded Python processing steps.
- Preserve Chat, Sources, Retrieval Lab, Evaluation, Settings and inspectable citations.

## Acceptance

Prove client isolation, session rotation, upload authorization, uploads above 4.5 MB,
retry idempotency, interrupted job recovery, and retrieval after restart. Re-run offline
regressions and live retrieval benchmarks. Production acceptance requires configured services,
email verification/recovery, and actual deployment smoke checks.

## Implementation order

Deployment structure; auth and isolation; private Blob adapter; hosted models; durable jobs;
Next.js workflows; regression and production verification. Never mark deployment complete
based solely on local tests. Hosted model migration requires reindexing and threshold calibration.

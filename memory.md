# Memory — Baseline Vector RAG and Pinecone Adapter

Last updated: 2026-09-01 23:24 CDT

## What was built

- Completed Phase 5 baseline vector RAG across PDF, website, and CSV sources.
- Added query contracts, BGE embeddings, top-k retrieval, source filtering, extractive answers, insufficient-evidence handling, grounded prompt construction, and request-local citations.
- Added durable FAISS generations with checksums, staged preparation, atomic activation, and restart-safe source discovery.
- Added `app/retrieval/pinecone_store.py` with workspace namespaces, batched staged generations, control-record activation, server-side filtering, compatibility validation, consistency retries, and best-effort stale-generation cleanup.
- Added Pinecone backend configuration and selection, documented setup in `README.md`, and added regression coverage in `tests/test_baseline_rag.py` and `tests/test_pinecone_store.py`.
- Updated `context/progress-tracker.md` to mark both vector-store adapters complete.

## Decisions made

- FAISS remains the default; Pinecone is selected explicitly with `VECTOR_STORE_BACKEND=pinecone`.
- The application connects to an existing Pinecone index and never creates or deletes it.
- Pinecone indexes must match the configured 384-dimensional model and cosine similarity.
- Each workspace has a Pinecone namespace. Complete generations are uploaded before a control record selects the active generation.
- Pinecone performs active-generation and source-ID filtering server-side.
- Old-generation cleanup is best-effort after activation and cannot roll back a valid generation.
- The adapter uses Pinecone's official asynchronous v9 SDK with bounded timeouts.
- Credentials remain environment-only and must never be committed or stored in memory.

## Problems solved

- Fixed macOS x86_64 incompatibilities with appropriate FAISS, Torch, NumPy, Transformers, and Sentence Transformers constraints.
- Prevented failed ingestions from activating partial indexes by separating preparation from activation and supporting READY-to-FAILED rollback.
- Prevented rebuilds after restart from dropping older sources by enumerating persisted metadata rather than relying only on the process-local registry.
- Corrected explicit indexing error handling, aligned query limits, and kept the embedding model lazily loaded.

## Current state

- Phase 5 is complete with FAISS and Pinecone adapters.
- Ruff, formatting, strict mypy, `git diff --check`, and all 89 tests pass.
- The working tree is clean at save time.
- Pinecone is tested with an in-memory async double. No live cloud smoke test was run because no credentials or provisioned test index were supplied.
- The registry remains process-local, although artifacts and indexes are durable and rebuilds discover persisted ready sources.

## Next session starts with

Run `/remember restore`, confirm this state, then either perform a live Pinecone smoke test against an existing 384-dimensional cosine index or use `/architect phase 6` for sentence-window retrieval.

## Open questions

- Whether to add an opt-in live Pinecone integration-test marker once a non-production test index is available.
- When to implement full durable source-registry rehydration instead of filesystem discovery during rebuilds.
- Phase 6 sentence-window parsing, neighbor-window metadata, and independent enablement require architecture decisions.

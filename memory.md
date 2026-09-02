# Memory — Foundation Through PDF Ingestion

Last updated: 2026-09-01 12:44 CDT

## What was built

- Completed Phases 0–2: uv-managed Python foundation, FastAPI/Streamlit applications, canonical source models, lifecycle-safe in-memory source registry, and end-to-end PDF ingestion.
- Added validated multipart PDF upload, PyMuPDF page extraction, whitespace normalization, page-bounded overlapping chunks, workspace-scoped artifact persistence, page locators, source/document inspection endpoints, and Streamlit upload/status UI.
- Added regression coverage for workspace isolation, lifecycle transitions, PDF validation, size limits, page metadata, artifact persistence, API ingestion, and injected storage failures.
- Recorded the PDF uploader and ingestion-status UI patterns in `context/ui-registry.md` and updated `context/progress-tracker.md` through Phase 2.

## Decisions made

- Use uv and Python 3.12+ with strict Ruff, mypy, and pytest gates.
- Use `workspace_id` consistently as the isolation boundary.
- Keep repository and storage behind async protocols; use in-memory source metadata and local filesystem artifacts until production persistence work.
- Parse PDFs with PyMuPDF; reject encrypted, malformed, oversized, scanned, and textless PDFs rather than adding OCR now.
- Use page-bounded chunks of about 1,200 characters with 200-character overlap, preserving one-based page locators.
- Keep storage locators backend-neutral strings so a later Azure Blob adapter does not change ingestion orchestration.

## Problems solved

- Closed lifecycle invariant bypasses for invalid initial statuses and null source updates.
- Reordered PDF finalization so durable ready metadata precedes the `ready` transition.
- Failure-state persistence errors are surfaced and logged instead of silently suppressed.
- Isolated PyMuPDF's incomplete typing and SWIG import warnings without weakening checks for project code.

## Current state

- Phase 2 is complete and its final review passed with no issues.
- Ruff, formatting, strict mypy, and all 27 tests pass.
- FastAPI and Streamlit were restarted with the current code and are available locally on ports 8000 and 8501 for this active environment.
- A manual PDF upload returned HTTP 201. Local artifacts persist under `data/`, which is intentionally ignored by Git.
- Source registry metadata remains process-local and is not rehydrated from saved artifacts after an API restart; durable registry persistence is deferred by design.

## Next session starts with

Run `/remember restore`, confirm this state, then use `/architect` to design Phase 3 website ingestion from `context/build-plan.md` before implementation.

## Open questions

- Define website crawl limits, robots-policy behavior, network safety/SSRF controls, extraction library, and same-domain rules during Phase 3 architecture.
- `QueryRequest`, `QueryResponse`, and `Citation` remain intentionally deferred to later query/generation and citation phases.

# Memory — DOCX Support and Graph Build Recovery

Last updated: 2026-09-19 00:00 CDT

## What was built

- Added DOCX support across the API, durable ingestion jobs, Next.js upload/download interface, and Streamlit. New files: `app/ingestion/docx.py`, `app/ingestion/docx_service.py`, and `tests/test_docx_ingestion.py`. Updated source/result/citation models, dependency wiring, storage adapters, migration inventory, evaluation locators, and README.
- Fixed graph-build feedback in `frontend/components/workspace.tsx`; added a mocked browser regression in `frontend/e2e/workspace.spec.ts`.

## Decisions made

- DOCX citations identify paragraph positions in document order, including blank/table paragraphs; no fabricated page numbers. Paragraph positions use the existing row identity field so graph support preserves them.
- Extract body/table paragraph text only. Headers, footers, comments, images/OCR, and legacy `.doc` are unsupported. Default upload limit is 25 MB; expanded archive limit 100 MB; individual XML part limit 20 MB. Existing PDF chunk settings also govern DOCX.
- Reconcile a lost graph-create response against the accepted job ID before showing an error. Disable new graph builds during active jobs; clear recovered polling errors separately from action errors and display graph progress as batches.

## Problems solved

- Local application startup needs sandbox escalation for embedded PostgreSQL's cache and listening ports. Use the existing authenticated launcher, not plain uvicorn, for the Next.js app.
- The reported graph-service error outlived the connection failure. The existing workflow recovered through retries and completed all five batches; confirmed local database status `complete`, step 7, and no error. No graph extraction algorithm change was needed.
- Browser regression against the dev server passed using `http://localhost:3000`; attempts using 127.0.0.1 stalled during workspace initialization and showed a blocked Next.js dev-origin warning.
- Automatic approval review rejected a separate diagnostic that would resend private document text to OpenAI. That diagnostic was not executed or bypassed. Read-only local checkpoint inspection was sufficient; the already-running user-initiated build finished independently.

## Current state

- All work remains uncommitted. Preserve the current working tree, including the pre-existing/generated `frontend/next-env.d.ts` change. No deployment or commit was requested.
- DOCX-focused and related ingestion/citation/routing/job tests passed: 63 tests before extending durable-job coverage; the expanded job suite subsequently passed all 9 cases, including DOCX for FAISS and Pinecone with test doubles.
- Ruff passed; mypy passed across app, UI, and the new DOCX tests (122 files). Frontend typecheck passed. Graph lost-response/duplicate-prevention Playwright regression passed. Full repository suite and live DOCX cloud-upload smoke test were not run.
- Authenticated API and Next.js dev server were running at localhost:8000 and localhost:3000 at handoff. Check health/processes before assuming they remain running. Local accounts/database persist in `.localdb`; never delete it as a routine restart step.
- Runtime profile uses PostgreSQL metadata/graphs, private Vercel Blob, OpenAI embeddings, and workflow dispatch. Secrets remain in existing environment-backed configuration; none are stored here.

## Next session starts with

Run `/remember restore`, inspect the uncommitted diff, and check application health. If starting services is needed, use `.venv/bin/python scripts/local_auth_stack.py` and `npm run dev --prefix frontend`. The latest user tasks are complete; wait for the next requested change. If preparing a release, run broader repository checks and review the diff before committing or deploying.

## Open questions

- No pending functional request. Live DOCX upload/extraction with the user's own files remains unverified; existing tests use synthetic documents and mocked services.
- The previous memory described Phase 12 in progress on September 4; it is stale relative to this session's repository. Do not resume that old plan without checking current code and context.

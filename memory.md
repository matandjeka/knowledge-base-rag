# Memory — Phase 12 Query Router In Progress

Last updated: 2026-09-04 09:30 CDT

## What was built

- Phase 11 structured database retrieval is complete and review fixes are applied. It includes
  credential-reference registration, PostgreSQL-first async SQLAlchemy access, read-only
  transactions, allowlisted schema inspection, structured OpenAI SQL generation, scope-aware
  SQLGlot validation, tenant predicates, bounded execution, database evidence, and citations.
- Phase 11 review regressions cover CTEs, aliases, nested DML rejection, preserved limits,
  qualified joined-row identities, PostgreSQL driver normalization, pooling, timeout setup, and
  sanitized URL errors. The established Streamlit baseline is saved in `ui-registry.md`.
- Phase 12 architecture is confirmed and implementation has started.
- Added routing contracts in `app/models/routing.py`, deterministic rules in
  `app/routing/rules.py`, routing metrics in `app/evaluation/routing_comparison.py`, and a committed
  12-question corpus in `tests/fixtures/routing_benchmark.json`.
- Added `RetrievalMode.AUTO`, optional response routing traces, router configuration, structured
  routing log fields, and automatic-plan execution/fallback logic in `QueryService`.
- Added focused Phase 12 coverage in `tests/test_query_routing.py`.

## Decisions made

- Automatic routing is opt-in through `retrieval_mode="auto"`; the API default remains `vector`.
- Routes use the smallest reliable plan: SQL alone, graph plus vector, lexical plus vector,
  sentence-window plus vector, or default fusion for weak/mixed intent.
- SQL may use an explicitly selected database or the sole ready workspace database. Multiple
  eligible databases must produce a selection-required error.
- Specialized routing requires both a confidence threshold and winning margin; ties fall back to
  default fusion.
- Explicit modes remain overrides and also receive an inspectable routing trace.
- The Phase 12 gate is 100% safe SQL behavior, at least 90% plan accuracy, no citation regression,
  at most 2% hit-rate/MRR decline, and improved specialized median latency.

## Problems solved

- Corrected the SQL comparison rule weight so “Compare revenue by region” clears the configured
  routing threshold without weakening generic-query fallback behavior.
- Added runtime graph-index fallback to default fusion while retaining an inspectable reason.
- Kept question text out of structured routing logs while exposing intent, confidence, selected
  retrievers, and fallback reason.

## Current state

- Phase 11 passed its final full verification with Ruff, strict mypy, `git diff --check`, and
  `151 passed, 1 skipped` tests.
- Phase 12 is partial. Ruff and strict mypy pass for the current code, and all 7 focused routing
  tests pass.
- The full test suite has not yet been run after the Phase 12 changes.
- Streamlit has not yet been updated to send automatic queries or display routing traces.
- `context/progress-tracker.md` has not yet been advanced to Phase 12.
- Current uncommitted changes are the Phase 12 files and edits shown by `git status`; preserve them.

## Next session starts with

Run `/remember restore`, confirm this checkpoint, then continue Phase 12 by running the full test
suite, resolving compatibility failures, updating Streamlit to query all available source types
with `retrieval_mode="auto"`, adding the routing-trace expander, and completing benchmark/API edge
coverage. Run `/imprint` after the UI change, then update `context/progress-tracker.md` only after
the complete quality suite passes.

## Open questions

- Verify whether automatic SQL with no ready database should remain a typed routing error or fall
  back to document fusion; multiple-database ambiguity is already locked as an error.
- Confirm benchmark metrics use at least one specialized observation before calculating median
  latency; add explicit validation if missing.
- Review the temporary cross-module routing model import between `app/models/query.py` and
  `app/models/routing.py`; it works and type-checks but may be cleaner if reorganized.

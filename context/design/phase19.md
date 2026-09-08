# Phase 19 — Enterprise Hardening

Approved September 8, 2026. Builds on committed Phase 18 (`main`). Ships as three
independently mergeable sub-milestones, each green on ruff / mypy / pytest.

- **19a — Identity & access:** organizations, memberships, roles, invitations,
  role-gated endpoints, repository-layer workspace isolation.
- **19b — Governance:** hash-chained audit log, per-workspace retention policies
  with a cascading purge CLI, source security labels with clearance-filtered retrieval.
- **19c — Abuse & content safety:** general rate limiting, indirect prompt-injection
  defenses + non-blocking ingestion content scan, per-workspace crawl domain allowlist,
  PII-redaction log filter.

Auth-dependent features (19a, plus audit/retention/labels in 19b) are production-mode
only. Content and abuse defenses in 19c apply in local (Streamlit, no-auth) mode too.

## 19a — Identity & access

### Model

- A **workspace** (`workspace_id`) remains the tenant data key on every source, vector,
  graph, job, and evaluation record.
- An **organization** owns exactly one workspace (`organizations.workspace_id`, unique).
- A **user** joins organizations through a **membership** carrying a **role**.
- Registration auto-creates a **personal organization** (`is_personal = true`,
  `workspace_id` = the value Phase 18 stored on the user) with the registrant as `owner`.
  The Phase 18 single-user experience is unchanged.
- Existing rows are backfilled by the `20260908_0003` migration: one personal org and
  one `owner` membership per user.

### Roles

Ordered: `viewer` < `member` < `admin` < `owner`.

| Capability | viewer | member | admin | owner |
|---|:-:|:-:|:-:|:-:|
| Query, read sources / evidence / jobs / evaluations / settings | ✓ | ✓ | ✓ | ✓ |
| Ingest (PDF / website / CSV / DB), reindex, create jobs, run evaluations | | ✓ | ✓ | ✓ |
| Invite members, change `member` / `viewer` roles, remove members | | | ✓ | ✓ |
| Read audit log, set retention, set security labels, set crawl allowlist (19b/19c) | | | ✓ | ✓ |
| Grant / revoke `admin` and `owner`, delete the organization | | | | ✓ |

The last `owner` of an organization cannot be demoted or removed.

### Request authorization

`authorize()` (global dependency) for an authenticated request targeting `workspace_id` W:

1. Resolve the organization that owns W. Unknown workspace → 404-shaped 403 ("Workspace access denied").
2. Resolve the caller's membership in that organization. No membership → 403.
3. Attach `request.state.membership` = `{org_id, workspace_id, role}`.
4. Reject requests that reference more than one distinct `workspace_id` (query, body, form).
5. Apply the role requirement for the request's method + path:
   - mutating `/sources/**`, `/graph/index`, `/sources/{id}/index`, `/jobs` (non-GET) → `member`
   - `/orgs/**` mutations → `admin` (endpoint enforces `owner` for role/delete escalation)
   - everything else (including `POST /query`) → any membership (`viewer`)

`/internal/**` (workflow callbacks) keep the shared-secret check and never touch memberships.
Local dev (`AUTH_ENABLED=false`) skips all of the above and pins `workspace = "local"`.

### Defense in depth

The workspace-scoped repositories (`PostgresPersistenceRepository`, source storage,
retrieval filters) already key every read and write by `workspace_id`. 19a enforces
"you are a member of this workspace" in the global `authorize()` dependency — which every
HTTP request passes through — and rejects any request naming more than one workspace.
19b's clearance filter lives in `QueryService` and is driven by the membership the route
resolved from `authorize()`. Non-HTTP callers (`rag-evaluate`, `rag-migrate-persistence`)
run with full access by design; the `max_classification` argument defaults to `restricted`
so a missing membership means "operator context", not a silent denial.

### Invitations

`rag_invitations` (`org_id`, `email`, `role`, single-use `token_hash`, 72h expiry).
`admin`+ creates an invitation; the email link carries the token. Acceptance requires an
authenticated session whose verified email matches the invitation; it creates the
membership and marks the invitation accepted. Reuses the Phase 18 mail webhook.

## 19b — Governance

### Audit log (`app/audit/`)

- `rag_audit_events`: append-only, one row per security-relevant action, `UniqueConstraint(org_id, sequence)`.
- **Hash chain**: `hash = sha256(canonical(org, sequence, actor, action, target, ip, metadata, created_at, prev_hash))`.
  `prev_hash` of the first event is the literal `"genesis"`. `created_at` is stored as an ISO-8601
  string so the digest is roundtrip-stable across SQLite and PostgreSQL.
- `AuditRepository` exposes `append`, `read`, `verify_chain`, and `purge_before` only — no update
  or arbitrary delete. `purge_before` removes a **contiguous oldest prefix** (retention only) and
  `verify_chain` **anchors to the first surviving event**: it still re-hashes and re-links every
  remaining row (so any altered, middle-deleted, or tail-deleted row fails) and reports the
  removed history as `pruned_through` rather than a break. The prefix removal is itself an audited,
  admin-gated action bounded by the 30-day floor.
- `record(request, action, ...)` is a best-effort emitter (never raises). Wired into org creation,
  invitations, joins, role/clearance changes, member removal, retention-policy changes, and
  source-classification changes. Auth-event and source-registration emission is deferred to 19c.
- `GET /audit/events` and `GET /audit/verify` require `admin`+. `rag-audit verify [--org-id]` CLI.
- Metadata is reduced to short scalars/lists (`_safe_metadata`) — no nested objects, strings
  capped at 256 chars.

### Retention (`app/retention/`)

- `rag_retention_policies` (per workspace) with independent day windows for source data, audit
  events, and query counters. All default `None` = keep forever.
- `rag_query_counts` (`workspace_id`, `day`, `count`) — a rolled-up counter incremented per
  `/query`, never storing question text.
- `apply_retention(...)` enforces every policy; `rag-retention apply [--dry-run]`. Audit purge is
  clamped to `AUDIT_RETENTION_FLOOR_DAYS = 30`. Every source purge writes a `retention.purged`
  audit event. Vector/graph generations are immutable and are not deleted here — a purged source
  is removed from the registry and object storage and its generations age out on the next reindex.
- `GET /retention` and `PUT /retention` require `admin`+.

### Security labels

- `Classification` (`public` < `internal` < `confidential` < `restricted`) on `Source`
  (`rag_sources.classification`, default `internal`); per-membership `clearance`
  (`rag_memberships.clearance`, default `internal`). `owner`/`admin` → `effective_clearance` is
  always `restricted`.
- Enforcement is in `QueryService`: `_clearance_scope()` computes the visible source-id set from
  the caller's clearance and the request filter, and every retriever call (including the auto
  router's source list) is restricted to it — so above-clearance content never enters the
  candidate set, fusion, re-ranking, the trace, citations, or the answer. A fully filtered result
  returns the normal insufficient-evidence response with no signal that restricted matches exist.
- `GET /sources` filters by clearance; `GET /sources/{id}/documents` returns 404 for an
  over-clearance source; `PATCH /sources/{id}` sets the label (`admin`+).

## 19c — Abuse & content safety

Applies in both authenticated and local (Streamlit) mode.

- **PII-redacting logs** — `app/core/redaction.py`: a `logging.Filter` on the root handler masks
  bearer tokens, `key: value` secrets, e-mail addresses, and 32+ char opaque strings in the
  rendered message and in every non-allowlisted string `extra`. Installed by `configure_logging`.
- **Rate limiting** — `app/core/rate_limit.py`: fixed-window per-caller counters (authenticated
  user id, else client IP), Postgres-backed (`rag_rate_limits`) with an in-process fallback.
  `rate_limit(bucket, setting)` dependency on `POST /query`, all ingestion routes, and `POST /jobs`;
  limits from `RATE_LIMIT_*` settings; 429 + `Retry-After`. Disable with `RATE_LIMIT_ENABLED=false`.
- **Prompt-injection defenses** (indirect) —
  - `app/ingestion/injection_scan.py`: a non-blocking heuristic scan runs on every ingested
    document set — both the synchronous `/sources/*` routes and the durable-jobs path
    (`app/jobs/processing.py`). Matches (`instruction-override`, `role-reassignment`,
    `role-markup`, `role-prefix`, `exfiltration-request`, `tool-call-injection`) are recorded in
    the source's `config.options["injection_flags"]` (visible in the inspector) and logged;
    ingestion is never blocked.
  - The OpenAI graph-extraction and NL→SQL callers wrap untrusted content in explicit
    `<documents>` / `<schema>` / `<question>` delimiters with a data-not-instructions directive.
    Structured output plus the existing `supporting_text`-is-a-substring check (graph) and the
    AST + allowlist validator (SQL) reject output that does not map to real input. No guard LLM.
- **Per-workspace crawl domain allowlist** — `rag_retention_policies.crawl_allowlist` (JSON).
  `GET/PUT /crawl-allowlist` (admin+). When set, both the synchronous website route and the
  durable-jobs crawl step reject any seed whose host is not an allowlist pattern (`example.com`
  also matches its subdomains); the always-on SSRF / non-global-IP guard remains beneath it.
- **Auth-event audit** — `POST /auth/login` writes `auth.login` / `auth.login_failed` to the
  account's personal-organization chain (best effort). Session-reuse-detected emission and the
  Streamlit/Next.js governance admin UI remain follow-ups.

## Threat model (maintained through 19c)

| Vector | Mitigation | Residual risk |
|---|---|---|
| Cross-tenant read via ID swap | `workspace_id` on every record; membership check in `authorize()`; single-workspace-per-request rule | A retriever invoked outside `authorize()` — covered by the 19b membership context |
| Privilege escalation via stale JWT | Role resolved server-side per request, never from the token | Access token valid up to 15 min after a session is revoked (Phase 18) |
| Last-owner lockout | Demote/remove of the final `owner` is refused | — |
| Indirect prompt injection (19c) | Delimited untrusted content, structured-output validation, non-blocking ingestion scan | A novel injection the heuristic misses reaches an LLM as delimited data only |
| Log exfiltration of PII / secrets (19c) | Allowlist formatter + redaction filter | A field explicitly added to the allowlist with sensitive content |

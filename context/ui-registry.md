# Advanced Multi-Source Enterprise RAG — UI Registry

## 1. Purpose
Canonical registry of UI components and their responsibilities.

## 2. Application Shell

### `AppShell`
Responsibilities:
- Global page layout
- Sidebar
- Header
- Main panel
- Optional source inspector

### `WorkspaceSelector`
Props/Data:
- workspace_id
- workspace_name
- role

## 3. Source Components

### `SourceTypePicker`
Options:
- PDF
- Website
- CSV
- Database

### `PdfUploader`
File: `ui/streamlit_app.py`
Last updated: 2026-09-01

Inputs:
- file(s)
- optional tags

Outputs:
- uploaded source IDs

| Property | Streamlit pattern |
| --- | --- |
| Container | `st.sidebar` source-management panel |
| Input | Labeled `st.file_uploader`, PDF constrained |
| Primary action | `st.button(type="primary", use_container_width=True)` |
| Supporting text | Input `help` text for limits and OCR behavior |
| Success feedback | `st.success` with the registered source name |
| Error feedback | `st.error` with actionable API-safe detail |

**Pattern notes:** Keep source upload controls in the sidebar. Require an explicit primary action after file selection, state limits in help text, and never display stack traces.

### `WebsiteSourceForm`
File: `ui/streamlit_app.py`
Last updated: 2026-09-01

Inputs:
- URL
- max pages
- same-domain crawl toggle

| Property | Streamlit pattern |
| --- | --- |
| Container | `st.sidebar` source-management panel, separated with `st.divider` |
| Inputs | Labeled `st.text_input`, `st.checkbox`, and bounded `st.number_input` |
| Primary action | `st.button(type="primary", use_container_width=True)` |
| Supporting text | Input `help` text for public-network, robots, and rendering boundaries |
| Success feedback | `st.success` with indexed page/chunk totals in `st.status` |
| Error feedback | `st.error` with actionable API-safe detail |

**Pattern notes:** Match the PDF source flow: keep source controls in the sidebar, require
an explicit primary action, disable conditional controls until relevant, and finish ingestion
with measurable ready/failed status. Do not imply that persisted chunks have vector embeddings.

### `CsvUploader`
File: `ui/streamlit_app.py`
Last updated: 2026-09-01

Inputs:
- file
- text columns
- metadata columns
- optional row-ID column

| Property | Streamlit pattern |
| --- | --- |
| Container | `st.sidebar` source-management panel, separated with `st.divider` |
| Upload | Labeled `st.file_uploader`, CSV constrained |
| Preview action | Full-width secondary `st.button` before configuration controls appear |
| Schema | Compact `st.dataframe` with column name and inferred type |
| Sample | Collapsed `st.expander` containing a bounded `st.dataframe` |
| Selection | Labeled `st.multiselect` controls and one `st.selectbox` for row identity |
| Primary action | `st.button(type="primary", use_container_width=True)`, disabled without text columns |
| Feedback | `st.status` with row/document/skipped counts, followed by `st.success` or `st.error` |

**Pattern notes:** Multi-step source forms reveal configuration only after validated preview.
Tie preview state to an upload digest so a replacement file cannot reuse stale schema. Keep
samples collapsed in the narrow sidebar, disable ingestion until required selections exist,
and report persisted row documents without implying vector indexing.

### `DatabaseConnectionForm`
Inputs:
- connection label
- host/DB reference through secrets
- approved schema/table selection

Never echo credentials after submission.

### `SourceCard`
Displays:
- title
- type
- status
- document/chunk count
- updated timestamp

Actions:
- inspect
- re-index
- delete

### `IngestionStatus`
File: `ui/streamlit_app.py`
Last updated: 2026-09-01

States:
- queued
- downloading
- parsing
- chunking
- embedding
- graphing
- indexing
- ready
- failed

| Property | Streamlit pattern |
| --- | --- |
| Active state | Expanded `st.status` using an action-oriented label |
| Progress detail | Short `status.write` lines with page and chunk counts |
| Success state | `state="complete"`, collapsed after completion |
| Error state | `state="error"`, kept visible with retry guidance |

**Pattern notes:** Status text must describe the current operation and finish in an explicit ready or failed state. Show measurable ingestion results when available; do not imply embedding or retrieval readiness before those phases exist.

## 4. Chat Components

### `ChatThread`
Renders ordered messages.

### `QuestionComposer`
Input:
- question
- optional source filters

### `AssistantAnswer`
Displays:
- answer markdown
- citations
- evidence state
- latency summary

### `CitationBadge`
Displays short locator and opens source inspector.

### `FollowUpSuggestions`
Displays generated follow-up questions that are grounded in available sources.

## 5. Evidence Components

### `SourceInspector`
Displays the original source excerpt and metadata.

### `EvidenceCard`
Fields:
- excerpt
- source
- retriever
- rank
- re-rank score

### `RetrieverBadge`
Allowed values:
- VECTOR
- WINDOW
- GRAPH
- BM25
- SQL

### `RetrievalTracePanel`
Advanced/debug-only view.
Displays:
- query route
- candidates by retriever
- fusion ranking
- re-ranking
- final context

## 6. Evaluation Components

### `EvaluationDashboard`
Metrics:
- recall@k
- MRR
- faithfulness
- citation accuracy
- latency
- cost/query

### `EvaluationRunCard`
Displays dataset, model, retriever configuration, score summary, and timestamp.

## 7. Settings Components

### `RetrieverSettings`
Controls:
- vector top-k
- sentence-window top-k
- graph depth/top-k
- BM25 top-k
- fusion strategy

### `GenerationSettings`
Controls:
- model
- temperature
- max answer length

Enterprise default:
- temperature near 0 for factual Q&A

### `SecuritySettings`
Controls:
- allowed website domains
- source retention
- tenant/workspace policies

## 8. Component Naming Rules
- Components: PascalCase
- Streamlit helper functions: snake_case
- Session state keys: `rag_<feature>_<name>`

Examples:
- `rag_chat_messages`
- `rag_active_workspace`
- `rag_selected_source`

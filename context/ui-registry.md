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
Inputs:
- file(s)
- optional tags

Outputs:
- uploaded source IDs

### `WebsiteSourceForm`
Inputs:
- URL
- max pages
- include patterns
- exclude patterns

### `CsvUploader`
Inputs:
- file
- text columns
- metadata columns

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

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

Every ingestion rebuilds two representations under one atomically activated workspace
generation:

- `vector` searches the existing normalized chunks and remains the default.
- `sentence_window` searches individual sentences, then returns the matched sentence with its
  neighboring context.

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

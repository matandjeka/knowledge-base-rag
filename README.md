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

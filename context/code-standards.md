# Advanced Multi-Source Enterprise RAG — Code Standards

## 1. Python Version
Use Python 3.12 or newer unless a dependency requires otherwise.

## 2. Style
- PEP 8
- Black-compatible formatting
- Ruff for linting
- Type hints required for public functions
- Prefer `pathlib.Path` over raw path strings
- Prefer dataclasses or Pydantic models for structured data

## 3. Naming

```text
modules/functions/variables: snake_case
classes: PascalCase
constants: UPPER_SNAKE_CASE
private helpers: _leading_underscore
```

## 4. Package Boundaries
Do not mix concerns.

Examples:
- Loaders must not call the LLM directly unless extraction requires it.
- Retrievers must not format UI.
- UI must not contain embedding logic.
- Citation formatting must not be buried inside retriever implementations.

## 5. Configuration
Use environment-driven configuration with Pydantic Settings.

Never commit:
- API keys
- database passwords
- Pinecone keys
- Azure secrets
- private URLs

Provide `.env.example`.

## 6. Data Models
Create canonical models such as:

```python
class SourceType(str, Enum):
    PDF = "pdf"
    WEBSITE = "website"
    CSV = "csv"
    DATABASE = "database"

class Evidence(BaseModel):
    evidence_id: str
    content: str
    retriever: str
    source_id: str
    source_type: SourceType
    score: float | None = None
    metadata: dict[str, Any] = {}
```

Avoid passing unstructured dictionaries between layers when a stable model exists.

## 7. Async Rules
Use async for:
- web requests
- database calls
- external model APIs
- ingestion orchestration

Do not force CPU-heavy embedding loops onto the event loop. Use worker pools or batch processing.

## 8. Logging
Use structured logs.

Every query should have:
- `trace_id`
- `workspace_id`
- `user_id` when available

Never log:
- raw credentials
- access tokens
- full sensitive database rows unless explicitly permitted

## 9. Error Handling
Define application exceptions:

```text
IngestionError
ParsingError
IndexingError
RetrievalError
RerankingError
GenerationError
SourceNotFoundError
UnauthorizedSourceError
```

Wrap dependency-specific exceptions before they escape domain modules.

## 10. Retrieval Contracts
Each retriever must implement a common interface.

```python
class Retriever(Protocol):
    async def retrieve(self, query: QueryRequest) -> list[Evidence]: ...
```

Each returned item must include enough metadata to build a citation.

## 11. Determinism
For enterprise Q&A:
- Keep generation temperature low
- Version prompts
- Version embedding models
- Version index schemas
- Record retrieval configuration in traces

## 12. Testing Standards

### Unit Tests
Required for:
- document normalization
- metadata filters
- fusion math
- de-duplication
- citation mapping
- SQL safety validation

### Integration Tests
Required for:
- PDF ingestion to index
- website ingestion to index
- CSV ingestion to index
- vector retrieval
- graph retrieval
- end-to-end query

### Evaluation Tests
Maintain a golden dataset:

```text
question
expected_source_ids
expected_answer_facts
forbidden_facts
```

Run evaluation before changing:
- chunk size
- embeddings
- fusion weights
- re-ranker
- prompt
- LLM

## 13. Security Standards
- Parameterized SQL only
- Read-only SQL accounts for RAG query tools
- Block DDL/DML in generated SQL
- Enforce tenant filter server-side
- Sanitize website ingestion
- Apply crawl limits
- Validate MIME type and file extension

## 14. Dependency Management
Prefer `pyproject.toml`.

Pin production dependencies with compatible constraints and use a lock file where supported.

Group dependencies:
- core
- dev
- evaluation
- optional graph
- optional UI

## 15. Documentation Standards
Every module should state:
- purpose
- inputs
- outputs
- major side effects

Public services require docstrings.

## 16. Git Standards
Branch naming:

```text
feature/pdf-ingestion
feature/graph-retriever
fix/citation-mapping
chore/upgrade-llamaindex
```

Commit messages:

```text
feat: add sentence-window retriever
fix: preserve page number in citations
test: add RRF ranking tests
```

## 17. Pull Request Definition of Done
A PR is complete only when:
- tests pass
- lint passes
- type checks pass
- docs updated
- new configuration documented
- no secrets committed
- evaluation impact reviewed for retrieval changes

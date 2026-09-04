"""Structured database registration, SQL safety, and evidence tests."""

import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.exceptions import DatabaseConfigurationError, SqlValidationError
from app.database import (
    DatabaseConnectionManager,
    DatabaseRegistrationService,
    DatabaseRetriever,
    SqlValidator,
)
from app.database.retrieval import _row_identifier
from app.generation.extractive import ExtractiveGenerator
from app.models import (
    DatabaseColumn,
    DatabaseDialect,
    DatabaseIsolationMode,
    DatabaseSourceRequest,
    DatabaseTablePolicy,
    DatabaseTableSchema,
    Evidence,
    GeneratedSql,
    QueryRequest,
    RetrievalMode,
)
from app.repositories import InMemorySourceRepository
from app.retrieval.query_service import QueryService


class DictSecretResolver:
    """Test resolver that keeps fixture URLs out of source metadata."""

    def __init__(self, secrets: Mapping[str, str]) -> None:
        self._secrets = secrets

    def resolve(self, reference: str) -> str:
        return self._secrets[reference]


class FixedSqlGenerator:
    """Return a deterministic SQL proposal."""

    def __init__(self, proposal: GeneratedSql) -> None:
        self._proposal = proposal
        self.schemas: Sequence[DatabaseTableSchema] = []

    async def generate(
        self,
        question: str,
        schemas: Sequence[DatabaseTableSchema],
        dialect: DatabaseDialect,
    ) -> GeneratedSql:
        del question, dialect
        self.schemas = schemas
        return self._proposal


class EmptyRetriever:
    async def retrieve(
        self,
        workspace_id: str,
        question: str,
        *,
        top_k: int,
        source_ids: frozenset[UUID],
        min_similarity: float = 0.0,
    ) -> list[Evidence]:
        del workspace_id, question, top_k, source_ids, min_similarity
        return []


def _schema(*, tenant_column: str | None = None) -> DatabaseTableSchema:
    return DatabaseTableSchema(
        table_name="sales",
        tenant_column=tenant_column,
        columns=[
            DatabaseColumn(name="id", data_type="INTEGER", nullable=False, primary_key=True),
            DatabaseColumn(name="workspace_id", data_type="TEXT", nullable=False),
            DatabaseColumn(name="region", data_type="TEXT", nullable=False),
            DatabaseColumn(name="amount", data_type="INTEGER", nullable=False),
        ],
    )


def _create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE sales "
            "(id INTEGER PRIMARY KEY, workspace_id TEXT, region TEXT, amount INTEGER)"
        )
        connection.executemany(
            "INSERT INTO sales VALUES (?, ?, ?, ?)",
            [
                (1, "workspace-a", "north", 10),
                (2, "workspace-b", "north", 999),
                (3, "workspace-a", "south", 15),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def test_validator_rejects_mutation_unknown_tables_and_unsafe_functions() -> None:
    validator = SqlValidator(max_rows=20)
    schema = [_schema()]
    for sql in (
        "DELETE FROM sales",
        "SELECT * FROM private_data",
        "SELECT pg_sleep(10) FROM sales",
        "SELECT missing FROM sales",
        "SELECT * FROM sales; SELECT * FROM sales",
        "SELECT * FROM sales WHERE amount = 10",
        "SELECT * FROM sales -- hide a second intent",
        "SELECT CURRENT_USER FROM sales",
    ):
        with pytest.raises(SqlValidationError):
            validator.validate(
                GeneratedSql(sql=sql),
                schema,
                DatabaseDialect.POSTGRESQL,
                workspace_tenant="workspace-a",
            )


def test_validator_injects_bound_tenant_filter_and_hard_limit() -> None:
    validated = SqlValidator(max_rows=25).validate(
        GeneratedSql(
            sql="SELECT id, amount FROM sales WHERE amount > :minimum",
            parameters={"minimum": 5},
        ),
        [_schema(tenant_column="workspace_id")],
        DatabaseDialect.POSTGRESQL,
        workspace_tenant="workspace-a",
    )

    assert "sales.workspace_id = :__workspace_tenant" in validated.sql
    assert validated.sql.endswith("AS _rag_bounded LIMIT 25")
    assert validated.parameters == {"minimum": 5, "__workspace_tenant": "workspace-a"}
    assert validated.referenced_tables == ["sales"]


def test_validator_supports_ctes_select_aliases_and_preserves_inner_limit() -> None:
    schema = [_schema()]
    validator = SqlValidator(max_rows=25)
    aggregate = validator.validate(
        GeneratedSql(
            sql=(
                "WITH totals AS ("
                "SELECT region, SUM(amount) AS total FROM sales GROUP BY region"
                ") SELECT region, total FROM totals ORDER BY total DESC"
            )
        ),
        schema,
        DatabaseDialect.POSTGRESQL,
        workspace_tenant="workspace-a",
    )
    limited = validator.validate(
        GeneratedSql(
            sql="SELECT id FROM sales ORDER BY id LIMIT :requested_limit",
            parameters={"requested_limit": 5},
        ),
        schema,
        DatabaseDialect.POSTGRESQL,
        workspace_tenant="workspace-a",
    )

    assert aggregate.referenced_tables == ["sales"]
    assert "ORDER BY total DESC" in aggregate.sql
    assert "LIMIT :requested_limit) AS _rag_bounded LIMIT 25" in limited.sql


def test_validator_explicitly_rejects_data_modifying_cte() -> None:
    with pytest.raises(SqlValidationError, match="DDL and DML"):
        SqlValidator(max_rows=25).validate(
            GeneratedSql(
                sql=(
                    "WITH removed AS (DELETE FROM sales RETURNING region) "
                    "SELECT region FROM removed"
                )
            ),
            [_schema()],
            DatabaseDialect.POSTGRESQL,
            workspace_tenant="workspace-a",
        )


def test_joined_row_identifiers_require_table_qualified_keys() -> None:
    schemas = [
        _schema(),
        DatabaseTableSchema(
            table_name="customers",
            columns=[
                DatabaseColumn(name="id", data_type="INTEGER", nullable=False, primary_key=True)
            ],
        ),
    ]

    assert (
        _row_identifier(
            {"sales__id": 1, "customers__id": 2},
            schemas,
            {"sales", "customers"},
        )
        == "sales.id=1, customers.id=2"
    )
    assert _row_identifier({"id": 1}, schemas, {"sales", "customers"}) is None


class _FakeTransaction:
    is_active = True

    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


class _FakeConnection:
    def __init__(self) -> None:
        self.transaction = _FakeTransaction()
        self.executions: list[tuple[str, dict[str, Any] | None]] = []

    async def begin(self) -> _FakeTransaction:
        return self.transaction

    async def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> None:
        self.executions.append((str(statement), parameters))


class _FakeConnectionContext:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self._connection

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeEngine:
    def __init__(self, connection: _FakeConnection | None = None) -> None:
        self.connection = connection or _FakeConnection()
        self.dispose_calls = 0

    def connect(self) -> _FakeConnectionContext:
        return _FakeConnectionContext(self.connection)

    async def dispose(self) -> None:
        self.dispose_calls += 1


@pytest.mark.asyncio
async def test_postgresql_connection_is_read_only_timed_and_pool_is_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_urls: list[str] = []
    engine = _FakeEngine()

    def create_engine(url: Any, *, pool_pre_ping: bool) -> AsyncEngine:
        assert pool_pre_ping
        created_urls.append(url.drivername)
        return cast(AsyncEngine, engine)

    monkeypatch.setattr("app.database.connection.create_async_engine", create_engine)
    manager = DatabaseConnectionManager(
        DictSecretResolver({"POSTGRES_URL": "postgres://reader:secret@db.example/sales"}),
        timeout_seconds=3,
    )

    async with manager.connect("POSTGRES_URL", DatabaseDialect.POSTGRESQL):
        pass
    async with manager.connect("POSTGRES_URL", DatabaseDialect.POSTGRESQL):
        pass

    assert created_urls == ["postgresql+asyncpg"]
    assert [sql for sql, _ in engine.connection.executions] == [
        "SET TRANSACTION READ ONLY",
        "SELECT set_config('statement_timeout', :timeout, true)",
        "SET TRANSACTION READ ONLY",
        "SELECT set_config('statement_timeout', :timeout, true)",
    ]
    assert engine.connection.executions[1][1] == {"timeout": "3000ms"}
    await manager.close()
    assert engine.dispose_calls == 1


@pytest.mark.asyncio
async def test_invalid_database_url_is_translated_without_secret_disclosure() -> None:
    manager = DatabaseConnectionManager(
        DictSecretResolver({"BROKEN_URL": "not a valid database url"}), timeout_seconds=3
    )

    with pytest.raises(DatabaseConfigurationError) as captured:
        async with manager.connect("BROKEN_URL", DatabaseDialect.POSTGRESQL):
            pass

    assert str(captured.value) == "Database connection URL is invalid"


@pytest.mark.asyncio
async def test_database_registration_and_shared_tenant_query(tmp_path: Path) -> None:
    database_path = tmp_path / "sales.sqlite3"
    _create_database(database_path)
    repository = InMemorySourceRepository()
    manager = DatabaseConnectionManager(
        DictSecretResolver({"TEST_DATABASE_URL": f"sqlite:///{database_path}"}),
        timeout_seconds=5,
    )
    registration = DatabaseRegistrationService(repository, manager, allow_sqlite=True)
    registered = await registration.register(
        DatabaseSourceRequest(
            workspace_id="workspace-a",
            name="Sales",
            secret_env_var="TEST_DATABASE_URL",
            dialect=DatabaseDialect.SQLITE,
            isolation_mode=DatabaseIsolationMode.SHARED,
            tables=[DatabaseTablePolicy(table_name="sales", tenant_column="workspace_id")],
        )
    )
    generator = FixedSqlGenerator(
        GeneratedSql(sql="SELECT id, region, amount FROM sales ORDER BY id")
    )
    database_retriever = DatabaseRetriever(
        repository,
        manager,
        generator,
        SqlValidator(max_rows=100),
        allow_sqlite=True,
    )
    empty = EmptyRetriever()
    service = QueryService(
        repository,
        empty,
        empty,
        ExtractiveGenerator(),
        default_top_k=5,
        max_top_k=20,
        min_similarity=0.7,
        database_retriever=database_retriever,
    )

    response = await service.query(
        QueryRequest(
            workspace_id="workspace-a",
            question="List sales",
            source_ids=[registered.source.source_id],
            retrieval_mode=RetrievalMode.SQL,
        )
    )

    assert [item.row_id for item in response.evidence] == ["sales.id=1", "sales.id=3"]
    assert all("999" not in item.content for item in response.evidence)
    assert [citation.locator for citation in response.citations] == [
        "table sales, record sales.id=1",
        "table sales, record sales.id=3",
    ]
    assert registered.source.config.options["secret_env_var"] == "TEST_DATABASE_URL"
    assert str(database_path) not in registered.source.model_dump_json()
    assert [column.name for column in generator.schemas[0].columns] == [
        "id",
        "workspace_id",
        "region",
        "amount",
    ]
    with pytest.raises(DatabaseConfigurationError, match="test adapter"):
        async with manager.connect("TEST_DATABASE_URL", DatabaseDialect.SQLITE):
            pass
    await manager.close()


@pytest.mark.asyncio
async def test_aggregate_result_becomes_one_query_citation(tmp_path: Path) -> None:
    database_path = tmp_path / "sales.sqlite3"
    _create_database(database_path)
    repository = InMemorySourceRepository()
    manager = DatabaseConnectionManager(
        DictSecretResolver({"TEST_DATABASE_URL": f"sqlite:///{database_path}"}),
        timeout_seconds=5,
    )
    registered = await DatabaseRegistrationService(repository, manager, allow_sqlite=True).register(
        DatabaseSourceRequest(
            workspace_id="workspace-a",
            name="Sales",
            secret_env_var="TEST_DATABASE_URL",
            dialect=DatabaseDialect.SQLITE,
            isolation_mode=DatabaseIsolationMode.DEDICATED,
            tables=[DatabaseTablePolicy(table_name="sales")],
        )
    )
    retriever = DatabaseRetriever(
        repository,
        manager,
        FixedSqlGenerator(GeneratedSql(sql="SELECT SUM(amount) AS total FROM sales")),
        SqlValidator(max_rows=100),
        allow_sqlite=True,
    )

    evidence = await retriever.retrieve(
        "workspace-a",
        "Total revenue?",
        top_k=5,
        source_ids=frozenset({registered.source.source_id}),
    )

    assert len(evidence) == 1
    assert evidence[0].metadata["aggregate"] is True
    assert '"total": 1024' in evidence[0].content
    assert evidence[0].row_id is None
    await manager.close()

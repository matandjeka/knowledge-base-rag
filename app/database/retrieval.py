"""Structured SQL retrieval and result-to-evidence conversion."""

import json
import time
from typing import Any
from uuid import UUID

from app.core.exceptions import RetrievalError
from app.database.connection import DatabaseConnectionManager
from app.database.generation import SqlGenerator
from app.database.registration import source_database_config
from app.database.validation import SqlValidator
from app.models import (
    DatabaseTableSchema,
    Evidence,
    Source,
    SourceStatus,
    SourceType,
    ValidatedSql,
)
from app.repositories import SourceRepository


class DatabaseRetriever:
    """Generate, validate, execute, and convert one database query."""

    def __init__(
        self,
        repository: SourceRepository,
        connections: DatabaseConnectionManager,
        generator: SqlGenerator,
        validator: SqlValidator,
        *,
        allow_sqlite: bool = False,
    ) -> None:
        self._repository = repository
        self._connections = connections
        self._generator = generator
        self._validator = validator
        self._allow_sqlite = allow_sqlite

    async def retrieve(
        self,
        workspace_id: str,
        question: str,
        *,
        top_k: int,
        source_ids: frozenset[UUID],
        min_similarity: float = 0.0,
    ) -> list[Evidence]:
        del min_similarity
        if len(source_ids) != 1:
            raise RetrievalError("SQL retrieval requires exactly one database source_id")
        source = await self._repository.get(workspace_id, next(iter(source_ids)))
        if source.config.source_type is not SourceType.DATABASE:
            raise RetrievalError("SQL retrieval requires a database source")
        if source.status is not SourceStatus.READY:
            raise RetrievalError("Database source is not ready")
        secret, dialect, _, _, schemas = source_database_config(source)
        proposal = await self._generator.generate(question, schemas, dialect)
        validated = self._validator.validate(
            proposal, schemas, dialect, workspace_tenant=workspace_id
        )
        started = time.perf_counter()
        rows = await self._connections.execute(
            secret,
            dialect,
            validated.sql,
            validated.parameters,
            allow_sqlite=self._allow_sqlite,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        return self._to_evidence(source, schemas, validated, rows, top_k, latency_ms)

    def _to_evidence(
        self,
        source: Source,
        schemas: list[DatabaseTableSchema],
        validated: ValidatedSql,
        rows: list[dict[str, Any]],
        top_k: int,
        latency_ms: float,
    ) -> list[Evidence]:
        if not rows:
            return []
        metadata = {
            "title": source.name,
            "database_name": source.name,
            "referenced_tables": validated.referenced_tables,
            "query_fingerprint": validated.query_fingerprint,
            "validated_sql": validated.sql,
            "returned_row_count": len(rows),
            "latency_ms": latency_ms,
        }
        table_name = ", ".join(validated.referenced_tables)
        if validated.is_aggregate:
            return [
                Evidence(
                    retriever="sql",
                    content=json.dumps(rows, default=str, sort_keys=True),
                    source_id=source.source_id,
                    source_type=SourceType.DATABASE,
                    raw_score=1.0,
                    normalized_score=1.0,
                    source_uri=source.config.uri,
                    table_name=table_name,
                    metadata={**metadata, "aggregate": True},
                )
            ]
        evidence: list[Evidence] = []
        for row in rows[:top_k]:
            row_id = _row_identifier(row, schemas, set(validated.referenced_tables))
            evidence.append(
                Evidence(
                    retriever="sql",
                    content=json.dumps(row, default=str, sort_keys=True),
                    source_id=source.source_id,
                    source_type=SourceType.DATABASE,
                    raw_score=1.0,
                    normalized_score=1.0,
                    source_uri=source.config.uri,
                    row_id=row_id,
                    table_name=table_name,
                    metadata={**metadata, "aggregate": False},
                )
            )
        return evidence


def _row_identifier(
    row: dict[str, Any],
    schemas: list[DatabaseTableSchema],
    referenced_tables: set[str],
) -> str | None:
    """Build an unambiguous table-qualified identifier when selected keys permit it."""
    relevant = [schema for schema in schemas if schema.qualified_name in referenced_tables]
    key_counts: dict[str, int] = {}
    for schema in relevant:
        for column in schema.columns:
            if column.primary_key:
                key_counts[column.name] = key_counts.get(column.name, 0) + 1
    identifiers: list[str] = []
    for schema in relevant:
        for column in schema.columns:
            if not column.primary_key:
                continue
            qualified_alias = f"{schema.qualified_name.replace('.', '__')}__{column.name}"
            table_alias = f"{schema.table_name}__{column.name}"
            candidates = [qualified_alias, table_alias]
            if key_counts[column.name] == 1:
                candidates.append(column.name)
            selected = next((candidate for candidate in candidates if candidate in row), None)
            if selected is not None:
                identifiers.append(f"{schema.qualified_name}.{column.name}={row[selected]}")
    return ", ".join(identifiers) or None

"""Allowlist-scoped relational schema introspection."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.exceptions import DatabaseConfigurationError
from app.models import DatabaseColumn, DatabaseTablePolicy, DatabaseTableSchema


async def inspect_allowed_tables(
    connection: AsyncConnection, policies: Sequence[DatabaseTablePolicy]
) -> list[DatabaseTableSchema]:
    """Inspect only explicitly approved tables and validate tenant columns."""
    return await connection.run_sync(_inspect_sync, policies)


def _inspect_sync(
    sync_connection: Any, policies: Sequence[DatabaseTablePolicy]
) -> list[DatabaseTableSchema]:
    inspector = inspect(sync_connection)
    schemas: list[DatabaseTableSchema] = []
    for policy in policies:
        available = inspector.get_table_names(schema=policy.schema_name)
        if policy.table_name not in available:
            raise DatabaseConfigurationError(
                f"Allowlisted table {policy.qualified_name!r} does not exist"
            )
        primary_keys = set(
            inspector.get_pk_constraint(policy.table_name, schema=policy.schema_name).get(
                "constrained_columns"
            )
            or []
        )
        columns = [
            DatabaseColumn(
                name=column["name"],
                data_type=str(column["type"]),
                nullable=bool(column.get("nullable", True)),
                primary_key=column["name"] in primary_keys,
            )
            for column in inspector.get_columns(policy.table_name, schema=policy.schema_name)
        ]
        if policy.tenant_column and policy.tenant_column not in {column.name for column in columns}:
            raise DatabaseConfigurationError(
                f"Tenant column {policy.tenant_column!r} does not exist on "
                f"{policy.qualified_name!r}"
            )
        schemas.append(
            DatabaseTableSchema(
                schema_name=policy.schema_name,
                table_name=policy.table_name,
                columns=columns,
                tenant_column=policy.tenant_column,
            )
        )
    return schemas

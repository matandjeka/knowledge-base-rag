"""Typed contracts for safe structured database retrieval."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.sources import Source


class DatabaseDialect(StrEnum):
    """Database dialects understood by the structured retrieval layer."""

    POSTGRESQL = "postgresql"
    SQLITE = "sqlite"


class DatabaseIsolationMode(StrEnum):
    """Supported workspace isolation strategies for external data."""

    DEDICATED = "dedicated"
    SHARED = "shared"


class DatabaseTablePolicy(BaseModel):
    """One explicitly queryable table and its tenant boundary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_name: str | None = Field(default=None, min_length=1, max_length=128)
    table_name: str = Field(min_length=1, max_length=128)
    tenant_column: str | None = Field(default=None, min_length=1, max_length=128)

    @property
    def qualified_name(self) -> str:
        """Return a stable schema-qualified table identifier."""
        return f"{self.schema_name}.{self.table_name}" if self.schema_name else self.table_name


class DatabaseSourceRequest(BaseModel):
    """Safe database registration payload containing no credentials."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workspace_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)
    secret_env_var: str = Field(min_length=1, max_length=128, pattern=r"^[A-Z][A-Z0-9_]*$")
    dialect: DatabaseDialect = DatabaseDialect.POSTGRESQL
    isolation_mode: DatabaseIsolationMode
    tables: list[DatabaseTablePolicy] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_isolation(self) -> "DatabaseSourceRequest":
        """Require tenant columns exactly when tables are shared."""
        names = [table.qualified_name for table in self.tables]
        if len(names) != len(set(names)):
            raise ValueError("Database table allowlist entries must be unique")
        if self.isolation_mode is DatabaseIsolationMode.SHARED:
            if any(table.tenant_column is None for table in self.tables):
                raise ValueError("Shared tables require a tenant_column")
        elif any(table.tenant_column is not None for table in self.tables):
            raise ValueError("Dedicated sources must not declare tenant columns")
        return self


class DatabaseColumn(BaseModel):
    """An introspected column exposed to the SQL generator."""

    model_config = ConfigDict(extra="forbid")

    name: str
    data_type: str
    nullable: bool
    primary_key: bool = False


class DatabaseTableSchema(BaseModel):
    """Approved table metadata safe to place in a model prompt."""

    model_config = ConfigDict(extra="forbid")

    schema_name: str | None = None
    table_name: str
    columns: list[DatabaseColumn]
    tenant_column: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}" if self.schema_name else self.table_name


class DatabaseSourceResult(BaseModel):
    """Registered database source and its approved schema."""

    model_config = ConfigDict(extra="forbid")

    source: Source
    tables: list[DatabaseTableSchema]


class GeneratedSql(BaseModel):
    """Strict output requested from the natural-language-to-SQL model."""

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1, max_length=20_000)
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class ValidatedSql(BaseModel):
    """Executable SQL after policy validation and server-side rewriting."""

    model_config = ConfigDict(extra="forbid")

    sql: str
    parameters: dict[str, Any]
    referenced_tables: list[str]
    query_fingerprint: str
    is_aggregate: bool = False

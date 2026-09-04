"""AST-aware validation and tenant-safe rewriting for generated SQL."""

import hashlib
import re
from collections.abc import Sequence

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import traverse_scope

from app.core.exceptions import SqlValidationError
from app.models import DatabaseDialect, DatabaseTableSchema, GeneratedSql, ValidatedSql

_SAFE_ANONYMOUS_FUNCTIONS = {"DATE_TRUNC"}
_SAFE_FUNCTION_TYPES = (
    exp.Abs,
    exp.AggFunc,
    exp.Cast,
    exp.Coalesce,
    exp.Concat,
    exp.CurrentDate,
    exp.DateTrunc,
    exp.Extract,
    exp.Lower,
    exp.Nullif,
    exp.Round,
    exp.Upper,
)
_PYFORMAT_PARAMETER = re.compile(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s")
_TENANT_PARAMETER = "__workspace_tenant"
_FORBIDDEN_EXPRESSIONS = (
    exp.Alter,
    exp.Command,
    exp.Create,
    exp.Delete,
    exp.Drop,
    exp.Insert,
    exp.Merge,
    exp.Transaction,
    exp.Update,
)


class SqlValidator:
    """Validate generated SQL against schema and isolation policy."""

    def __init__(self, *, max_rows: int) -> None:
        self._max_rows = max_rows

    def validate(
        self,
        proposal: GeneratedSql,
        schemas: Sequence[DatabaseTableSchema],
        dialect: DatabaseDialect,
        *,
        workspace_tenant: str,
    ) -> ValidatedSql:
        """Return one safe, bounded SQL statement or reject the proposal."""
        sqlglot_dialect = "postgres" if dialect is DatabaseDialect.POSTGRESQL else dialect.value
        try:
            statements = sqlglot.parse(proposal.sql, read=sqlglot_dialect)
        except ParseError as error:
            raise SqlValidationError("Generated SQL could not be parsed") from error
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            raise SqlValidationError("SQL must contain exactly one SELECT statement")
        statement = statements[0]
        if any(statement.find(kind) is not None for kind in _FORBIDDEN_EXPRESSIONS):
            raise SqlValidationError("DDL and DML are not allowed anywhere in SQL")
        if statement.find(exp.Lock) or statement.args.get("into") is not None:
            raise SqlValidationError("Locking and SELECT INTO are not allowed")
        if any(node.comments for node in statement.walk()):
            raise SqlValidationError("SQL comments are not allowed")
        if statement.find(exp.Literal) or statement.find(exp.Boolean):
            raise SqlValidationError("SQL values must use named parameters")

        policy_by_name = self._policies_by_name(schemas)
        referenced = self._validate_scopes(statement, policy_by_name)
        self._validate_functions(statement)

        placeholders = {node.name for node in statement.find_all(exp.Placeholder)}
        supplied = set(proposal.parameters)
        if _TENANT_PARAMETER in supplied:
            raise SqlValidationError("Reserved tenant parameter cannot be supplied")
        if placeholders != supplied:
            raise SqlValidationError("SQL placeholders and supplied parameters must match")

        uses_tenant_filter = self._inject_tenant_filters(statement, policy_by_name)
        parameters = dict(proposal.parameters)
        if uses_tenant_filter:
            parameters[_TENANT_PARAMETER] = workspace_tenant
        inner_sql = statement.sql(dialect=sqlglot_dialect)
        inner_sql = _PYFORMAT_PARAMETER.sub(r":\1", inner_sql)
        normalized = f"SELECT * FROM ({inner_sql}) AS _rag_bounded LIMIT {self._max_rows}"
        fingerprint = hashlib.sha256(normalized.encode()).hexdigest()
        is_aggregate = any(True for _ in statement.find_all(exp.AggFunc))
        return ValidatedSql(
            sql=normalized,
            parameters=parameters,
            referenced_tables=sorted(referenced),
            query_fingerprint=fingerprint,
            is_aggregate=is_aggregate,
        )

    def _policies_by_name(
        self, schemas: Sequence[DatabaseTableSchema]
    ) -> dict[str, DatabaseTableSchema]:
        policies: dict[str, DatabaseTableSchema] = {}
        table_name_counts: dict[str, int] = {}
        for schema in schemas:
            table_name_counts[schema.table_name] = table_name_counts.get(schema.table_name, 0) + 1
        for schema in schemas:
            policies[schema.qualified_name] = schema
            if table_name_counts[schema.table_name] == 1:
                policies[schema.table_name] = schema
        return policies

    def _validate_scopes(
        self, statement: exp.Select, policies: dict[str, DatabaseTableSchema]
    ) -> set[str]:
        referenced: set[str] = set()
        for scope in traverse_scope(statement):
            source_columns: dict[str, set[str]] = {}
            for alias, source in scope.sources.items():
                if isinstance(source, exp.Table):
                    name = f"{source.db}.{source.name}" if source.db else source.name
                    policy = policies.get(name)
                    if policy is None:
                        raise SqlValidationError(f"Table {name!r} is not allowlisted")
                    referenced.add(policy.qualified_name)
                    source_columns[alias] = {column.name for column in policy.columns}
                else:
                    source_columns[alias] = set(source.expression.named_selects)
            allowed_in_scope = set().union(*source_columns.values()) if source_columns else set()
            for column in scope.columns:
                if column.name == "*":
                    continue
                if column.table:
                    permitted = source_columns.get(column.table)
                    if permitted is None:
                        raise SqlValidationError(
                            f"Column source {column.table!r} is not available in its SQL scope"
                        )
                else:
                    permitted = allowed_in_scope
                if column.name not in permitted:
                    raise SqlValidationError(f"Column {column.sql()!r} is not allowlisted")
        if not referenced:
            raise SqlValidationError("SQL must reference at least one allowlisted table")
        return referenced

    def _validate_functions(self, statement: exp.Select) -> None:
        for function in statement.find_all(exp.Func):
            if isinstance(function, _SAFE_FUNCTION_TYPES):
                continue
            if isinstance(function, exp.Anonymous):
                name = function.name.upper()
                if name in _SAFE_ANONYMOUS_FUNCTIONS:
                    continue
            raise SqlValidationError(f"SQL function {type(function).__name__!r} is not allowed")

    def _inject_tenant_filters(
        self, statement: exp.Select, policies: dict[str, DatabaseTableSchema]
    ) -> bool:
        injected = False
        for scope in traverse_scope(statement):
            predicates: list[exp.Expression] = []
            for alias, (_, source) in scope.selected_sources.items():
                if not isinstance(source, exp.Table):
                    continue
                name = f"{source.db}.{source.name}" if source.db else source.name
                policy = policies[name]
                if policy.tenant_column:
                    predicates.append(
                        exp.EQ(
                            this=exp.column(policy.tenant_column, table=alias),
                            expression=exp.Placeholder(this=_TENANT_PARAMETER),
                        )
                    )
            for predicate in predicates:
                scope.expression.where(predicate, append=True, copy=False)
                injected = True
        return injected

"""Per-workspace data retention policies and their enforcement.

Inputs: an ``AsyncEngine`` plus retention windows in days (``None`` = keep forever).
Outputs: ``RetentionPolicy`` rows and a ``RetentionReport`` describing what a purge removed.
Side effects: increments query counters; deletes expired audit prefixes, query counters,
and source metadata/artifacts when a purge runs (never on ``--dry-run``).
"""

import contextlib
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, Column, DateTime, Integer, String, Table, delete, func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.audit import AUDIT_RETENTION_FLOOR_DAYS, AuditRepository
from app.persistence.metadata import metadata

retention_policies_table = Table(
    "rag_retention_policies",
    metadata,
    Column("workspace_id", String(64), primary_key=True),
    Column("source_data_days", Integer),
    Column("audit_days", Integer),
    Column("query_log_days", Integer),
    Column("crawl_allowlist", JSON),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
query_counts_table = Table(
    "rag_query_counts",
    metadata,
    Column("workspace_id", String(64), primary_key=True),
    Column("day", String(10), primary_key=True),
    Column("count", Integer, nullable=False),
)


class RetentionPolicy(BaseModel):
    """Retention windows for one workspace. ``None`` means keep indefinitely."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    source_data_days: int | None = Field(default=None, ge=1, le=3650)
    audit_days: int | None = Field(default=None, ge=1, le=3650)
    query_log_days: int | None = Field(default=None, ge=1, le=3650)


class RetentionReport(BaseModel):
    """What a retention pass removed (or would remove, on a dry run)."""

    model_config = ConfigDict(extra="forbid")

    dry_run: bool
    audit_events_purged: int = 0
    query_counters_purged: int = 0
    sources_purged: list[str] = Field(default_factory=list)


class RetentionRepository:
    """Storage for retention policies and daily query counters."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self._dialect = engine.dialect.name

    async def get(self, workspace_id: str) -> RetentionPolicy:
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(retention_policies_table).where(
                            retention_policies_table.c.workspace_id == workspace_id
                        )
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return RetentionPolicy(workspace_id=workspace_id)
        return RetentionPolicy(
            workspace_id=workspace_id,
            source_data_days=row["source_data_days"],
            audit_days=row["audit_days"],
            query_log_days=row["query_log_days"],
        )

    async def set(self, policy: RetentionPolicy) -> RetentionPolicy:
        values = {
            "workspace_id": policy.workspace_id,
            "source_data_days": policy.source_data_days,
            "audit_days": policy.audit_days,
            "query_log_days": policy.query_log_days,
            "updated_at": datetime.now(UTC),
        }
        insert_stmt = (
            postgres_insert(retention_policies_table)
            if self._dialect == "postgresql"
            else sqlite_insert(retention_policies_table)
        )
        async with self.engine.begin() as conn:
            await conn.execute(
                insert_stmt.values(**values).on_conflict_do_update(
                    index_elements=["workspace_id"],
                    set_={
                        key: values[key]
                        for key in (
                            "source_data_days",
                            "audit_days",
                            "query_log_days",
                            "updated_at",
                        )
                    },
                )
            )
        return policy

    async def crawl_allowlist(self, workspace_id: str) -> list[str] | None:
        """Domain patterns a workspace may crawl, or ``None`` when unrestricted."""
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    select(retention_policies_table.c.crawl_allowlist).where(
                        retention_policies_table.c.workspace_id == workspace_id
                    )
                )
            ).scalar_one_or_none()
        return list(row) if row else None

    async def set_crawl_allowlist(
        self, workspace_id: str, patterns: list[str] | None
    ) -> list[str] | None:
        values = {
            "workspace_id": workspace_id,
            "crawl_allowlist": patterns,
            "updated_at": datetime.now(UTC),
        }
        insert_stmt = (
            postgres_insert(retention_policies_table)
            if self._dialect == "postgresql"
            else sqlite_insert(retention_policies_table)
        )
        async with self.engine.begin() as conn:
            await conn.execute(
                insert_stmt.values(**values).on_conflict_do_update(
                    index_elements=["workspace_id"],
                    set_={"crawl_allowlist": patterns, "updated_at": values["updated_at"]},
                )
            )
        return patterns

    async def all_policies(self) -> list[RetentionPolicy]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(retention_policies_table))).mappings().all()
        return [
            RetentionPolicy(
                workspace_id=row["workspace_id"],
                source_data_days=row["source_data_days"],
                audit_days=row["audit_days"],
                query_log_days=row["query_log_days"],
            )
            for row in rows
        ]

    async def record_query(self, workspace_id: str) -> None:
        today = date.today().isoformat()
        insert_stmt = (
            postgres_insert(query_counts_table)
            if self._dialect == "postgresql"
            else sqlite_insert(query_counts_table)
        )
        async with self.engine.begin() as conn:
            await conn.execute(
                insert_stmt.values(
                    workspace_id=workspace_id, day=today, count=1
                ).on_conflict_do_update(
                    index_elements=["workspace_id", "day"],
                    set_={"count": query_counts_table.c.count + 1},
                )
            )

    async def purge_query_counts(self, workspace_id: str, cutoff_day: str, *, commit: bool) -> int:
        async with self.engine.begin() as conn:
            matched = (
                await conn.execute(
                    select(func.count())
                    .select_from(query_counts_table)
                    .where(
                        query_counts_table.c.workspace_id == workspace_id,
                        query_counts_table.c.day < cutoff_day,
                    )
                )
            ).scalar_one()
            if commit and matched:
                await conn.execute(
                    delete(query_counts_table).where(
                        query_counts_table.c.workspace_id == workspace_id,
                        query_counts_table.c.day < cutoff_day,
                    )
                )
        return int(matched)


async def apply_retention(
    *,
    retention: RetentionRepository,
    audit: AuditRepository,
    source_repository: Any,
    source_storage: Any,
    organization_lookup: Any,
    dry_run: bool = False,
    now: datetime | None = None,
) -> list[RetentionReport]:
    """Enforce every configured policy, cascading source deletion through metadata + storage.

    Vector and graph generations are immutable and replaced rather than deleted in the current
    design; a purged source is removed from the registry and object storage, and its retrieval
    generations age out when the workspace is next reindexed.
    """
    moment = now or datetime.now(UTC)
    reports: list[RetentionReport] = []
    for policy in await retention.all_policies():
        if (
            policy.source_data_days is None
            and policy.audit_days is None
            and policy.query_log_days is None
        ):
            continue
        report = RetentionReport(dry_run=dry_run)
        org_id = await organization_lookup(policy.workspace_id)

        if policy.audit_days is not None and org_id is not None:
            effective = max(policy.audit_days, AUDIT_RETENTION_FLOOR_DAYS)
            cutoff = moment - timedelta(days=effective)
            report.audit_events_purged = await audit.purge_before(
                org_id=org_id, cutoff=cutoff, commit=not dry_run
            )

        if policy.query_log_days is not None:
            cutoff_day = (moment - timedelta(days=policy.query_log_days)).date().isoformat()
            report.query_counters_purged = await retention.purge_query_counts(
                policy.workspace_id, cutoff_day, commit=not dry_run
            )

        if policy.source_data_days is not None:
            cutoff = moment - timedelta(days=policy.source_data_days)
            for source in await source_repository.list(policy.workspace_id):
                if source.updated_at >= cutoff:
                    continue
                report.sources_purged.append(str(source.source_id))
                if dry_run:
                    continue
                await source_repository.delete(policy.workspace_id, source.source_id)
                remover = getattr(source_storage, "delete_source", None)
                if remover is not None:
                    with contextlib.suppress(Exception):
                        await remover(policy.workspace_id, source.source_id)
                if org_id is not None:
                    await audit.append(
                        org_id=org_id,
                        action="retention.purged",
                        workspace_id=policy.workspace_id,
                        target_type="source",
                        target_id=str(source.source_id),
                        metadata={"reason": "source_data_days", "days": policy.source_data_days},
                    )
        reports.append(report)
    return reports

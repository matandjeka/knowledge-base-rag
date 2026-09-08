"""Phase 19b — hash-chained audit log, retention enforcement, and clearance-filtered retrieval."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import create_async_engine

from app.audit import AuditRepository, audit_events_table
from app.auth.organizations import Role
from app.models import Classification, Source, SourceConfig, SourceStatus, SourceType
from app.persistence.metadata import metadata
from app.repositories import InMemorySourceRepository
from app.retention import (
    RetentionPolicy,
    RetentionReport,
    RetentionRepository,
    apply_retention,
)


@pytest.fixture
async def engine(tmp_path: Any) -> Any:
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/gov.db")
    async with eng.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_audit_chain_detects_tampering(engine: Any) -> None:
    repo = AuditRepository(engine)
    for i in range(3):
        await repo.append(org_id="org-1", action="test.event", metadata={"i": i})
    assert (await repo.verify_chain("org-1")).ok

    async with engine.begin() as conn:
        await conn.execute(
            update(audit_events_table)
            .where(audit_events_table.c.org_id == "org-1", audit_events_table.c.sequence == 2)
            .values(action="test.tampered")
        )
    tampered = await repo.verify_chain("org-1")
    assert not tampered.ok and tampered.first_broken_sequence == 2


@pytest.mark.asyncio
async def test_audit_metadata_is_reduced_to_safe_scalars(engine: Any) -> None:
    repo = AuditRepository(engine)
    event = await repo.append(
        org_id="org-2",
        action="test.event",
        metadata={"role": "admin", "count": 3, "blob": {"secret": "x"}, "long": "y" * 500},
    )
    assert event.metadata == {"role": "admin", "count": 3, "long": "y" * 256}


@pytest.mark.asyncio
async def test_retention_purges_query_counts_but_honours_the_audit_floor(engine: Any) -> None:
    retention = RetentionRepository(engine)
    audit = AuditRepository(engine)
    now = datetime(2026, 9, 8, tzinfo=UTC)

    # One workspace with an aggressive 1-day policy on everything.
    await retention.set(
        RetentionPolicy(workspace_id="ws", source_data_days=1, audit_days=1, query_log_days=1)
    )
    async with engine.begin() as conn:
        from app.retention import query_counts_table

        await conn.execute(
            query_counts_table.insert().values(workspace_id="ws", day="2026-08-01", count=9)
        )

    async def lookup(workspace_id: str) -> str | None:
        return "org-ws"

    # 10-day-old audit event: policy says 1 day, but the 30-day floor protects it.
    await audit.append(
        org_id="org-ws", action="seed", workspace_id="ws", at=now - timedelta(days=10)
    )

    reports = await apply_retention(
        retention=retention,
        audit=audit,
        source_repository=InMemorySourceRepository(),
        source_storage=object(),
        organization_lookup=lookup,
        now=now,
    )
    assert reports[0] == RetentionReport(
        dry_run=False, audit_events_purged=0, query_counters_purged=1, sources_purged=[]
    )
    assert (await audit.verify_chain("org-ws")).ok


@pytest.mark.asyncio
async def test_audit_purge_prunes_a_prefix_and_the_chain_still_verifies(engine: Any) -> None:
    retention = RetentionRepository(engine)
    audit = AuditRepository(engine)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    # Five events: three older than the 30-day floor, two recent.
    for age in (60, 55, 50, 5, 1):
        await audit.append(org_id="o", action="e", workspace_id="ws", at=now - timedelta(days=age))
    await retention.set(RetentionPolicy(workspace_id="ws", audit_days=1))

    async def lookup(_: str) -> str | None:
        return "o"

    dry = await apply_retention(
        retention=retention,
        audit=audit,
        source_repository=InMemorySourceRepository(),
        source_storage=object(),
        organization_lookup=lookup,
        dry_run=True,
        now=now,
    )
    assert dry[0].audit_events_purged == 3
    assert (await audit.verify_chain("o")).pruned_through is None  # dry run changed nothing

    live = await apply_retention(
        retention=retention,
        audit=audit,
        source_repository=InMemorySourceRepository(),
        source_storage=object(),
        organization_lookup=lookup,
        now=now,
    )
    assert live[0].audit_events_purged == 3
    verification = await audit.verify_chain("o")
    assert verification.ok and verification.pruned_through == 3

    # A surviving row cannot be altered undetected after the prune.
    async with engine.begin() as conn:
        await conn.execute(
            update(audit_events_table)
            .where(audit_events_table.c.org_id == "o", audit_events_table.c.sequence == 5)
            .values(action="tampered")
        )
    assert not (await audit.verify_chain("o")).ok


@pytest.mark.asyncio
async def test_retention_deletes_expired_sources_and_audits_the_purge(engine: Any) -> None:
    retention = RetentionRepository(engine)
    audit = AuditRepository(engine)
    sources = InMemorySourceRepository()
    now = datetime(2026, 9, 8, tzinfo=UTC)

    stale = Source(
        workspace_id="ws",
        name="old.pdf",
        config=SourceConfig(source_type=SourceType.PDF),
        created_at=now - timedelta(days=90),
        updated_at=now - timedelta(days=90),
    )
    await sources.create(stale)
    await sources.transition("ws", stale.source_id, SourceStatus.INDEXING)
    await sources.transition(
        "ws", stale.source_id, SourceStatus.READY, transitioned_at=now - timedelta(days=90)
    )
    await retention.set(RetentionPolicy(workspace_id="ws", source_data_days=30))

    async def lookup(workspace_id: str) -> str | None:
        return "org-ws"

    dry = await apply_retention(
        retention=retention,
        audit=audit,
        source_repository=sources,
        source_storage=object(),
        organization_lookup=lookup,
        dry_run=True,
        now=now,
    )
    assert dry[0].sources_purged == [str(stale.source_id)]
    assert await sources.list("ws")  # dry run kept it

    await apply_retention(
        retention=retention,
        audit=audit,
        source_repository=sources,
        source_storage=object(),
        organization_lookup=lookup,
        now=now,
    )
    assert list(await sources.list("ws")) == []
    events = await audit.read(org_id="org-ws")
    assert any(e.action == "retention.purged" for e in events)


@pytest.mark.asyncio
async def test_clearance_filters_sources_and_documents(engine: Any) -> None:
    from app.api.routes.sources import list_source_documents, list_sources

    sources = InMemorySourceRepository()
    secret = Source(
        workspace_id="ws",
        name="secret.pdf",
        config=SourceConfig(source_type=SourceType.PDF),
        classification=Classification.CONFIDENTIAL,
    )
    public = Source(
        workspace_id="ws",
        name="public.pdf",
        config=SourceConfig(source_type=SourceType.PDF),
        classification=Classification.INTERNAL,
    )
    for source in (secret, public):
        await sources.create(source)

    class _State:
        pass

    class _Request:
        def __init__(self, clearance: Classification) -> None:
            self.state = _State()
            self.state.membership = _Membership(clearance)

    class _Membership:
        def __init__(self, clearance: Classification) -> None:
            self.role = Role.MEMBER
            self.clearance = clearance

        @property
        def effective_clearance(self) -> Classification:
            return self.clearance

    internal_view = await list_sources("ws", _Request(Classification.INTERNAL), sources)  # type: ignore[arg-type]
    assert {s.name for s in internal_view} == {"public.pdf"}

    class _Storage:
        async def load_documents(self, *_: Any) -> list[Any]:
            return []

    with pytest.raises(Exception):  # noqa: B017 - HTTPException 404 for an over-clearance source
        await list_source_documents(
            secret.source_id,
            "ws",
            _Request(Classification.INTERNAL),  # type: ignore[arg-type]
            sources,
            _Storage(),  # type: ignore[arg-type]
        )

"""Append-only, hash-chained audit log.

Inputs: an ``AsyncEngine`` and structured, already-safe event fields.
Outputs: ``AuditEvent`` rows whose ``hash`` links each entry to its predecessor.
Side effects: rows in ``rag_audit_events``. The repository exposes append, read, verify,
and retention purge only — never update or arbitrary delete.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    JSON,
    Column,
    Integer,
    String,
    Table,
    UniqueConstraint,
    delete,
    func,
    insert,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.persistence.metadata import metadata

logger = logging.getLogger(__name__)

GENESIS_HASH = "genesis"
# Audit history is retained at least this long regardless of a shorter retention policy,
# so the hash chain stays continuous and verifiable.
AUDIT_RETENTION_FLOOR_DAYS = 30

audit_events_table = Table(
    "rag_audit_events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("org_id", String(36), nullable=False, index=True),
    Column("sequence", Integer, nullable=False),
    Column("workspace_id", String(64)),
    Column("actor_user_id", String(36)),
    Column("action", String(64), nullable=False),
    Column("target_type", String(64)),
    Column("target_id", String(128)),
    Column("ip", String(64)),
    Column("event_metadata", JSON, nullable=False),
    Column("created_at", String(32), nullable=False),
    Column("prev_hash", String(64), nullable=False),
    Column("hash", String(64), nullable=False),
    UniqueConstraint("org_id", "sequence"),
)


class AuditEvent(BaseModel):
    """One immutable audit record."""

    model_config = ConfigDict(extra="forbid")

    id: str
    org_id: str
    sequence: int
    workspace_id: str | None = None
    actor_user_id: str | None = None
    action: str
    target_type: str | None = None
    target_id: str | None = None
    ip: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    prev_hash: str
    hash: str


class ChainVerification(BaseModel):
    """Result of walking an organization's audit hash chain."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    first_broken_sequence: int | None = None
    pruned_through: int | None = None


def _canonical(
    *,
    org_id: str,
    sequence: int,
    workspace_id: str | None,
    actor_user_id: str | None,
    action: str,
    target_type: str | None,
    target_id: str | None,
    ip: str | None,
    event_metadata: dict[str, Any],
    created_at: str,
    prev_hash: str,
) -> str:
    return json.dumps(
        {
            "org_id": org_id,
            "sequence": sequence,
            "workspace_id": workspace_id,
            "actor_user_id": actor_user_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "ip": ip,
            "metadata": event_metadata,
            "created_at": created_at,
            "prev_hash": prev_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(canonical: str) -> str:
    return hashlib.sha256(canonical.encode()).hexdigest()


class AuditRepository:
    """Hash-chained append-only storage for security-relevant events."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def append(
        self,
        *,
        org_id: str,
        action: str,
        workspace_id: str | None = None,
        actor_user_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        ip: str | None = None,
        metadata: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> AuditEvent:
        payload = _safe_metadata(metadata or {})
        created_at = (at or datetime.now(UTC)).isoformat()
        for _ in range(4):
            try:
                return await self._append_once(
                    org_id=org_id,
                    action=action,
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    target_type=target_type,
                    target_id=target_id,
                    ip=ip,
                    event_metadata=payload,
                    created_at=created_at,
                )
            except IntegrityError:
                continue
        raise RuntimeError("Audit sequence contention did not resolve")

    async def _append_once(
        self,
        *,
        org_id: str,
        action: str,
        workspace_id: str | None,
        actor_user_id: str | None,
        target_type: str | None,
        target_id: str | None,
        ip: str | None,
        event_metadata: dict[str, Any],
        created_at: str,
    ) -> AuditEvent:
        async with self.engine.begin() as conn:
            last = (
                (
                    await conn.execute(
                        select(audit_events_table.c.sequence, audit_events_table.c.hash)
                        .where(audit_events_table.c.org_id == org_id)
                        .order_by(audit_events_table.c.sequence.desc())
                        .limit(1)
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            sequence = (last["sequence"] + 1) if last else 1
            prev_hash = last["hash"] if last else GENESIS_HASH
            canonical = _canonical(
                org_id=org_id,
                sequence=sequence,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                ip=ip,
                event_metadata=event_metadata,
                created_at=created_at,
                prev_hash=prev_hash,
            )
            event = AuditEvent(
                id=str(uuid4()),
                org_id=org_id,
                sequence=sequence,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                ip=ip,
                metadata=event_metadata,
                created_at=created_at,
                prev_hash=prev_hash,
                hash=_digest(canonical),
            )
            await conn.execute(
                insert(audit_events_table).values(
                    id=event.id,
                    org_id=org_id,
                    sequence=sequence,
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    ip=ip,
                    event_metadata=event_metadata,
                    created_at=created_at,
                    prev_hash=prev_hash,
                    hash=event.hash,
                )
            )
            return event

    async def read(
        self, *, org_id: str, limit: int = 50, before: int | None = None
    ) -> list[AuditEvent]:
        statement = (
            select(audit_events_table)
            .where(audit_events_table.c.org_id == org_id)
            .order_by(audit_events_table.c.sequence.desc())
            .limit(min(max(limit, 1), 200))
        )
        if before is not None:
            statement = statement.where(audit_events_table.c.sequence < before)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(statement)).mappings().all()
        return [_event_from_row(dict(row)) for row in rows]

    async def verify_chain(self, org_id: str) -> "ChainVerification":
        """Recompute and re-link every stored event.

        Verification anchors to the first *surviving* event: retention may delete an oldest
        contiguous prefix (that removal is itself an audited, admin-only action), which shows up
        as ``pruned_through`` rather than a break. Any altered row, any deleted row from the
        middle or end, or any gap in the sequence still fails.
        """
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        select(audit_events_table)
                        .where(audit_events_table.c.org_id == org_id)
                        .order_by(audit_events_table.c.sequence.asc())
                    )
                )
                .mappings()
                .all()
            )
        if not rows:
            return ChainVerification(ok=True)
        first_sequence = int(rows[0]["sequence"])
        pruned_through = first_sequence - 1 if first_sequence > 1 else None
        prev_hash = rows[0]["prev_hash"] if first_sequence > 1 else GENESIS_HASH
        expected_sequence = first_sequence
        for row in rows:
            if row["sequence"] != expected_sequence or row["prev_hash"] != prev_hash:
                return ChainVerification(
                    ok=False,
                    first_broken_sequence=int(row["sequence"]),
                    pruned_through=pruned_through,
                )
            canonical = _canonical(
                org_id=row["org_id"],
                sequence=row["sequence"],
                workspace_id=row["workspace_id"],
                actor_user_id=row["actor_user_id"],
                action=row["action"],
                target_type=row["target_type"],
                target_id=row["target_id"],
                ip=row["ip"],
                event_metadata=row["event_metadata"],
                created_at=row["created_at"],
                prev_hash=row["prev_hash"],
            )
            if _digest(canonical) != row["hash"]:
                return ChainVerification(
                    ok=False,
                    first_broken_sequence=int(row["sequence"]),
                    pruned_through=pruned_through,
                )
            prev_hash = row["hash"]
            expected_sequence += 1
        return ChainVerification(ok=True, pruned_through=pruned_through)

    async def purge_before(self, *, org_id: str, cutoff: datetime, commit: bool = True) -> int:
        """Delete the oldest contiguous run of events created before ``cutoff``.

        Only a prefix of the chain is removed. ``verify_chain`` reports the surviving
        history as ``pruned_through`` and still re-links and re-hashes every remaining row,
        so tampering with the middle or the end is still detected. With ``commit=False``
        the count that *would* be removed is returned without deleting anything.
        """
        async with self.engine.begin() as conn:
            rows = (
                (
                    await conn.execute(
                        select(audit_events_table.c.sequence, audit_events_table.c.created_at)
                        .where(audit_events_table.c.org_id == org_id)
                        .order_by(audit_events_table.c.sequence.asc())
                    )
                )
                .mappings()
                .all()
            )
            cutoff_iso = cutoff.isoformat()
            removable = 0
            for row in rows:
                if row["created_at"] >= cutoff_iso:
                    break
                removable += 1
            if removable == 0 or not commit:
                return removable
            boundary = rows[removable - 1]["sequence"]
            await conn.execute(
                delete(audit_events_table).where(
                    audit_events_table.c.org_id == org_id,
                    audit_events_table.c.sequence <= boundary,
                )
            )
        return removable

    async def organization_ids(self) -> list[str]:
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(select(func.distinct(audit_events_table.c.org_id)))
            ).scalars()
        return list(rows)


def _safe_metadata(value: dict[str, Any]) -> dict[str, Any]:
    """Keep audit metadata to short, non-sensitive scalars."""
    safe: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, (bool, int)):
            safe[key] = item
        elif isinstance(item, str):
            safe[key] = item[:256]
        elif isinstance(item, list):
            safe[key] = [str(entry)[:64] for entry in item[:20]]
    return safe


def _event_from_row(row: dict[str, Any]) -> AuditEvent:
    event_metadata = row.pop("event_metadata", {})
    return AuditEvent.model_validate({**row, "metadata": event_metadata})


async def record(
    request: Request,
    action: str,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    org_id: str | None = None,
    workspace_id: str | None = None,
) -> None:
    """Best-effort audit emit from within a request; never raises."""
    from app.api.dependencies import get_metadata_engine

    membership = getattr(request.state, "membership", None)
    resolved_org = org_id or (membership.org_id if membership else None)
    if resolved_org is None:
        return
    user = getattr(request.state, "user", None)
    try:
        await AuditRepository(get_metadata_engine()).append(
            org_id=resolved_org,
            action=action,
            workspace_id=workspace_id or (membership.workspace_id if membership else None),
            actor_user_id=user["id"] if user else None,
            target_type=target_type,
            target_id=target_id,
            ip=request.client.host if request.client else None,
            metadata=metadata,
        )
    except Exception:
        logger.warning("Audit append failed", extra={"action": action})

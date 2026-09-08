"""Organizations, memberships, roles, and invitations.

Inputs: an ``AsyncEngine`` bound to the metadata database and plain identifiers.
Outputs: validated ``Organization`` / ``Membership`` / ``OrgMember`` / ``Invitation`` models.
Side effects: rows in ``rag_organizations``, ``rag_memberships``, ``rag_invitations``.
"""

import secrets
import time
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Table,
    UniqueConstraint,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.service import digest, users
from app.models import Classification
from app.persistence.metadata import metadata


class Role(StrEnum):
    """Organization roles in ascending privilege order."""

    VIEWER = "viewer"
    MEMBER = "member"
    ADMIN = "admin"
    OWNER = "owner"


_RANK: dict[Role, int] = {Role.VIEWER: 0, Role.MEMBER: 1, Role.ADMIN: 2, Role.OWNER: 3}
INVITATION_TTL_SECONDS = 72 * 3600


def role_allows(actual: Role, required: Role) -> bool:
    """Return whether ``actual`` satisfies the ``required`` minimum role."""
    return _RANK[actual] >= _RANK[required]


organizations_table = Table(
    "rag_organizations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("workspace_id", String(64), nullable=False, unique=True),
    Column("is_personal", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
memberships_table = Table(
    "rag_memberships",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("org_id", String(36), nullable=False, index=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("role", String(16), nullable=False),
    Column("clearance", String(16), nullable=False, server_default="internal"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("org_id", "user_id"),
)
invitations_table = Table(
    "rag_invitations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("org_id", String(36), nullable=False, index=True),
    Column("email", String(254), nullable=False),
    Column("role", String(16), nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("invited_by", String(36), nullable=False),
    Column("expires", Integer, nullable=False),
    Column("accepted", Boolean, nullable=False, default=False),
    UniqueConstraint("org_id", "email"),
)


class Organization(BaseModel):
    """One tenant that owns exactly one workspace."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    workspace_id: str
    is_personal: bool
    created_at: datetime


class Membership(BaseModel):
    """A user's role and clearance within one organization."""

    model_config = ConfigDict(extra="forbid")

    org_id: str
    workspace_id: str
    user_id: str
    role: Role
    clearance: Classification = Classification.INTERNAL

    @property
    def effective_clearance(self) -> Classification:
        """Owners and admins always hold the highest clearance."""
        if self.role in {Role.OWNER, Role.ADMIN}:
            return Classification.RESTRICTED
        return self.clearance


class OrgMember(BaseModel):
    """A member row enriched with the account email for admin listings."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    email: str
    role: Role
    clearance: Classification
    created_at: datetime


class Invitation(BaseModel):
    """A pending, single-use organization invitation."""

    model_config = ConfigDict(extra="forbid")

    id: str
    org_id: str
    email: str
    role: Role
    invited_by: str
    accepted: bool


class LastOwnerError(HTTPException):
    """Refuses to demote or remove the final owner of an organization."""

    def __init__(self) -> None:
        super().__init__(409, "An organization must keep at least one owner.")


class OrganizationRepository:
    """Transactional organization, membership, and invitation storage."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def create_personal_organization(
        self, *, user_id: str, email: str, workspace_id: str
    ) -> Organization:
        """Create the personal organization backfilled/created for a new account."""
        return await self._create(
            name=f"{email}'s workspace",
            workspace_id=workspace_id,
            owner_user_id=user_id,
            is_personal=True,
        )

    async def create_organization(self, *, name: str, owner_user_id: str) -> Organization:
        """Create a shared organization with a fresh workspace and a single owner."""
        return await self._create(
            name=name,
            workspace_id=str(uuid4()),
            owner_user_id=owner_user_id,
            is_personal=False,
        )

    async def _create(
        self, *, name: str, workspace_id: str, owner_user_id: str, is_personal: bool
    ) -> Organization:
        org = Organization(
            id=str(uuid4()),
            name=name,
            workspace_id=workspace_id,
            is_personal=is_personal,
            created_at=datetime.now(UTC),
        )
        try:
            async with self.engine.begin() as conn:
                await conn.execute(insert(organizations_table).values(**org.model_dump()))
                await conn.execute(
                    insert(memberships_table).values(
                        id=str(uuid4()),
                        org_id=org.id,
                        user_id=owner_user_id,
                        role=Role.OWNER.value,
                        created_at=datetime.now(UTC),
                    )
                )
        except IntegrityError:
            raise HTTPException(409, "That organization already exists.") from None
        return org

    async def organization_for_workspace(self, workspace_id: str) -> Organization | None:
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(organizations_table).where(
                            organizations_table.c.workspace_id == workspace_id
                        )
                    )
                )
                .mappings()
                .first()
            )
        return Organization.model_validate(dict(row)) if row else None

    async def membership(self, *, user_id: str, workspace_id: str) -> Membership | None:
        """Resolve a caller's membership in the organization owning ``workspace_id``."""
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(
                            memberships_table.c.org_id,
                            memberships_table.c.user_id,
                            memberships_table.c.role,
                            memberships_table.c.clearance,
                            organizations_table.c.workspace_id,
                        )
                        .join(
                            organizations_table,
                            organizations_table.c.id == memberships_table.c.org_id,
                        )
                        .where(
                            organizations_table.c.workspace_id == workspace_id,
                            memberships_table.c.user_id == user_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
        return Membership.model_validate(dict(row)) if row else None

    async def membership_by_org(self, *, user_id: str, org_id: str) -> Membership | None:
        """Resolve a caller's membership by organization id (for ``/orgs`` endpoints)."""
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(
                            memberships_table.c.org_id,
                            memberships_table.c.user_id,
                            memberships_table.c.role,
                            memberships_table.c.clearance,
                            organizations_table.c.workspace_id,
                        )
                        .join(
                            organizations_table,
                            organizations_table.c.id == memberships_table.c.org_id,
                        )
                        .where(
                            memberships_table.c.org_id == org_id,
                            memberships_table.c.user_id == user_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
        return Membership.model_validate(dict(row)) if row else None

    async def organizations_for_user(self, user_id: str) -> list[Organization]:
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        select(organizations_table)
                        .join(
                            memberships_table,
                            memberships_table.c.org_id == organizations_table.c.id,
                        )
                        .where(memberships_table.c.user_id == user_id)
                        .order_by(organizations_table.c.created_at)
                    )
                )
                .mappings()
                .all()
            )
        return [Organization.model_validate(dict(row)) for row in rows]

    async def members(self, org_id: str) -> list[OrgMember]:
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        select(
                            memberships_table.c.user_id,
                            users.c.email,
                            memberships_table.c.role,
                            memberships_table.c.clearance,
                            memberships_table.c.created_at,
                        )
                        .join(users, users.c.id == memberships_table.c.user_id)
                        .where(memberships_table.c.org_id == org_id)
                        .order_by(memberships_table.c.created_at)
                    )
                )
                .mappings()
                .all()
            )
        return [OrgMember.model_validate(dict(row)) for row in rows]

    async def set_role(self, *, org_id: str, user_id: str, role: Role) -> None:
        async with self.engine.begin() as conn:
            current = await self._locked_role(conn, org_id, user_id)
            if current is None:
                raise HTTPException(404, "That member was not found.")
            if (
                current is Role.OWNER
                and role is not Role.OWNER
                and await self._owner_count(conn, org_id) <= 1
            ):
                raise LastOwnerError()
            await conn.execute(
                update(memberships_table)
                .where(
                    memberships_table.c.org_id == org_id,
                    memberships_table.c.user_id == user_id,
                )
                .values(role=role.value)
            )

    async def set_clearance(self, *, org_id: str, user_id: str, clearance: Classification) -> None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                update(memberships_table)
                .where(
                    memberships_table.c.org_id == org_id,
                    memberships_table.c.user_id == user_id,
                )
                .values(clearance=clearance.value)
            )
        if result.rowcount == 0:
            raise HTTPException(404, "That member was not found.")

    async def remove_member(self, *, org_id: str, user_id: str) -> None:
        async with self.engine.begin() as conn:
            current = await self._locked_role(conn, org_id, user_id)
            if current is None:
                raise HTTPException(404, "That member was not found.")
            if current is Role.OWNER and await self._owner_count(conn, org_id) <= 1:
                raise LastOwnerError()
            await conn.execute(
                delete(memberships_table).where(
                    memberships_table.c.org_id == org_id,
                    memberships_table.c.user_id == user_id,
                )
            )

    async def create_invitation(
        self, *, org_id: str, email: str, role: Role, invited_by: str
    ) -> tuple[Invitation, str]:
        token = secrets.token_urlsafe(48)
        invitation = Invitation(
            id=str(uuid4()),
            org_id=org_id,
            email=email.strip().lower(),
            role=role,
            invited_by=invited_by,
            accepted=False,
        )
        try:
            async with self.engine.begin() as conn:
                await conn.execute(
                    delete(invitations_table).where(
                        invitations_table.c.org_id == org_id,
                        invitations_table.c.email == invitation.email,
                        invitations_table.c.accepted.is_(False),
                    )
                )
                await conn.execute(
                    insert(invitations_table).values(
                        id=invitation.id,
                        org_id=org_id,
                        email=invitation.email,
                        role=role.value,
                        token_hash=digest(token),
                        invited_by=invited_by,
                        expires=int(time.time()) + INVITATION_TTL_SECONDS,
                        accepted=False,
                    )
                )
        except IntegrityError:
            raise HTTPException(409, "That address is already a member.") from None
        return invitation, token

    async def accept_invitation(self, *, token: str, user_id: str, email: str) -> Organization:
        async with self.engine.begin() as conn:
            row = (
                (
                    await conn.execute(
                        select(invitations_table)
                        .where(invitations_table.c.token_hash == digest(token))
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if (
                row is None
                or row["accepted"]
                or row["expires"] < int(time.time())
                or row["email"] != email.strip().lower()
            ):
                raise HTTPException(400, "This invitation is invalid or has expired.")
            org = (
                (
                    await conn.execute(
                        select(organizations_table).where(organizations_table.c.id == row["org_id"])
                    )
                )
                .mappings()
                .one()
            )
            try:
                await conn.execute(
                    insert(memberships_table).values(
                        id=str(uuid4()),
                        org_id=row["org_id"],
                        user_id=user_id,
                        role=row["role"],
                        created_at=datetime.now(UTC),
                    )
                )
            except IntegrityError:
                raise HTTPException(409, "You already belong to this organization.") from None
            await conn.execute(
                update(invitations_table)
                .where(invitations_table.c.id == row["id"])
                .values(accepted=True)
            )
        return Organization.model_validate(dict(org))

    async def _locked_role(self, conn: Any, org_id: str, user_id: str) -> Role | None:
        row = (
            await conn.execute(
                select(memberships_table.c.role)
                .where(
                    memberships_table.c.org_id == org_id,
                    memberships_table.c.user_id == user_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        return Role(row) if row is not None else None

    async def _owner_count(self, conn: Any, org_id: str) -> int:
        return int(
            (
                await conn.execute(
                    select(func.count())
                    .select_from(memberships_table)
                    .where(
                        memberships_table.c.org_id == org_id,
                        memberships_table.c.role == Role.OWNER.value,
                    )
                )
            ).scalar_one()
        )

"""Create organizations, memberships, and invitations; backfill personal organizations."""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import context, op

revision = "20260908_0003"
down_revision: str | None = "20260907_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    return context.get_x_argument(as_dictionary=True).get("schema", "rag")


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "rag_organizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=False, unique=True),
        sa.Column("is_personal", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    op.create_table(
        "rag_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False, index=True),
        sa.Column("user_id", sa.String(36), nullable=False, index=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("org_id", "user_id"),
        schema=schema,
    )
    op.create_table(
        "rag_invitations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False, index=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("invited_by", sa.String(36), nullable=False),
        sa.Column("expires", sa.Integer(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("org_id", "email"),
        schema=schema,
    )
    _backfill_personal_organizations(schema)


def downgrade() -> None:
    schema = _schema()
    for table in ("rag_invitations", "rag_memberships", "rag_organizations"):
        op.drop_table(table, schema=schema)


def _backfill_personal_organizations(schema: str) -> None:
    """Give every existing account a personal organization it owns."""
    bind = op.get_bind()
    users = sa.table(
        "rag_users",
        sa.column("id", sa.String),
        sa.column("email", sa.String),
        sa.column("workspace_id", sa.String),
        schema=schema,
    )
    organizations = sa.table(
        "rag_organizations",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("workspace_id", sa.String),
        sa.column("is_personal", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        schema=schema,
    )
    memberships = sa.table(
        "rag_memberships",
        sa.column("id", sa.String),
        sa.column("org_id", sa.String),
        sa.column("user_id", sa.String),
        sa.column("role", sa.String),
        sa.column("created_at", sa.DateTime),
        schema=schema,
    )
    now = datetime.now(UTC)
    for row in bind.execute(sa.select(users.c.id, users.c.email, users.c.workspace_id)):
        org_id = str(uuid4())
        bind.execute(
            organizations.insert().values(
                id=org_id,
                name=f"{row.email}'s workspace",
                workspace_id=row.workspace_id,
                is_personal=True,
                created_at=now,
            )
        )
        bind.execute(
            memberships.insert().values(
                id=str(uuid4()),
                org_id=org_id,
                user_id=row.id,
                role="owner",
                created_at=now,
            )
        )

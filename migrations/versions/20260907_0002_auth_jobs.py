"""Create client accounts, sessions, and durable job tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision = "20260907_0002"
down_revision: str | None = "20260906_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    return context.get_x_argument(as_dictionary=True).get("schema", "rag")


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "rag_users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=False, unique=True),
        sa.Column("verified", sa.Boolean(), nullable=False),
        schema=schema,
    )
    op.create_table(
        "rag_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False, index=True),
        sa.Column("refresh_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires", sa.Integer(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        schema=schema,
    )
    op.create_table(
        "rag_refresh_history",
        sa.Column("hash", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        schema=schema,
    )
    op.create_table(
        "rag_auth_limits",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires", sa.Integer(), nullable=False),
        schema=schema,
    )
    op.create_table(
        "rag_email_tokens",
        sa.Column("hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("expires", sa.Integer(), nullable=False),
        schema=schema,
    )
    op.create_table(
        "rag_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False, index=True),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(256)),
        schema=schema,
    )
    op.create_table(
        "rag_workspace_jobs",
        sa.Column("workspace_id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(36), nullable=False, unique=True),
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    for table in (
        "rag_workspace_jobs",
        "rag_jobs",
        "rag_email_tokens",
        "rag_auth_limits",
        "rag_refresh_history",
        "rag_sessions",
        "rag_users",
    ):
        op.drop_table(table, schema=schema)

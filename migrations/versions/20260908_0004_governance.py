"""Audit log, retention policies, query counters, source labels, and member clearance."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision = "20260908_0004"
down_revision: str | None = "20260908_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    return context.get_x_argument(as_dictionary=True).get("schema", "rag")


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "rag_audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False, index=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.String(64)),
        sa.Column("actor_user_id", sa.String(36)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64)),
        sa.Column("target_id", sa.String(128)),
        sa.Column("ip", sa.String(64)),
        sa.Column("event_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("org_id", "sequence"),
        schema=schema,
    )
    op.create_table(
        "rag_retention_policies",
        sa.Column("workspace_id", sa.String(64), primary_key=True),
        sa.Column("source_data_days", sa.Integer()),
        sa.Column("audit_days", sa.Integer()),
        sa.Column("query_log_days", sa.Integer()),
        sa.Column("crawl_allowlist", sa.JSON()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    op.create_table(
        "rag_rate_limits",
        sa.Column("key", sa.String(160), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires", sa.Integer(), nullable=False),
        schema=schema,
    )
    op.create_table(
        "rag_query_counts",
        sa.Column("workspace_id", sa.String(64), primary_key=True),
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        schema=schema,
    )
    op.add_column(
        "rag_sources",
        sa.Column("classification", sa.String(16), nullable=False, server_default="internal"),
        schema=schema,
    )
    op.add_column(
        "rag_memberships",
        sa.Column("clearance", sa.String(16), nullable=False, server_default="internal"),
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_column("rag_memberships", "clearance", schema=schema)
    op.drop_column("rag_sources", "classification", schema=schema)
    for table in (
        "rag_rate_limits",
        "rag_query_counts",
        "rag_retention_policies",
        "rag_audit_events",
    ):
        op.drop_table(table, schema=schema)

"""Create durable source, generation, operation, and migration metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision = "20260906_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    return context.get_x_argument(as_dictionary=True).get("schema", "rag")


def upgrade() -> None:
    schema = _schema()
    op.execute(sa.schema.CreateSchema(schema, if_not_exists=True))
    op.create_table(
        "rag_sources",
        sa.Column("source_id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False, index=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("workspace_id", "source_id"),
        schema=schema,
    )
    op.create_table(
        "rag_active_generations",
        sa.Column("workspace_id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("generation_id", sa.String(36), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    op.create_table(
        "rag_persistence_operations",
        sa.Column("operation_id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False, index=True),
        sa.Column("generation_id", sa.String(36), nullable=False),
        sa.Column("generation_kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("prepared_stores", sa.JSON(), nullable=False),
        sa.Column("error_category", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    op.create_table(
        "rag_migration_checkpoints",
        sa.Column("migration_id", sa.String(36), primary_key=True),
        sa.Column("item_key", sa.String(500), primary_key=True),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    for table in (
        "rag_migration_checkpoints",
        "rag_persistence_operations",
        "rag_active_generations",
        "rag_sources",
    ):
        op.drop_table(table, schema=schema)

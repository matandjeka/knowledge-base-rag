"""Store immutable graph snapshots in plain PostgreSQL."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision = "20260912_0005"
down_revision: str | None = "20260908_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rag_graph_snapshots",
        sa.Column("workspace_id", sa.String(64), primary_key=True),
        sa.Column("generation_id", sa.String(36), primary_key=True),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        schema=context.get_x_argument(as_dictionary=True).get("schema", "rag"),
    )


def downgrade() -> None:
    op.drop_table(
        "rag_graph_snapshots",
        schema=context.get_x_argument(as_dictionary=True).get("schema", "rag"),
    )

"""Separate story findings from table and workflow metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0012_analysis_lanes"
down_revision = "0011_compute_workers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_proposals",
        sa.Column("lane", sa.String(length=20), nullable=False, server_default="story"),
    )
    op.execute("UPDATE analysis_proposals SET lane = 'meta' WHERE kind = 'rule'")


def downgrade() -> None:
    op.drop_column("analysis_proposals", "lane")

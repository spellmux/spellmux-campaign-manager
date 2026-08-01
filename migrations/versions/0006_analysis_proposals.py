"""Add human-reviewed session analysis proposals."""

import sqlalchemy as sa
from alembic import op

revision = "0006_analysis_proposals"
down_revision = "0005_session_description"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("run_metadata", sa.JSON(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("reviewed_by_id", sa.Uuid(), nullable=True),
        sa.Column("promoted_guide_entry_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["game_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["promoted_guide_entry_id"], ["campaign_guide_entries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analysis_proposals_session_id", "analysis_proposals", ["session_id"])
    op.create_index("ix_analysis_proposals_session_status", "analysis_proposals", ["session_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_analysis_proposals_session_status", table_name="analysis_proposals")
    op.drop_index("ix_analysis_proposals_session_id", table_name="analysis_proposals")
    op.drop_table("analysis_proposals")

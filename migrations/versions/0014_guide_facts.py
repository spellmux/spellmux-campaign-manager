"""Add sourced facts to canonical campaign guide entries."""

import sqlalchemy as sa
from alembic import op

revision = "0014_guide_facts"
down_revision = "0013_session_chronicle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_guide_facts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("guide_entry_id", sa.Uuid(), sa.ForeignKey("campaign_guide_entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("game_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_proposal_id", sa.Uuid(), sa.ForeignKey("analysis_proposals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("category", sa.String(length=40), nullable=False, server_default="session_detail"),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="canonical"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("visibility", sa.String(length=20), nullable=False, server_default="gm"),
        sa.Column("created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_campaign_guide_facts_entry", "campaign_guide_facts", ["guide_entry_id", "status"])
    op.create_index("ix_campaign_guide_facts_guide_entry_id", "campaign_guide_facts", ["guide_entry_id"])
    op.create_index("ix_campaign_guide_facts_session_id", "campaign_guide_facts", ["session_id"])
    op.create_index("ix_campaign_guide_facts_source_proposal_id", "campaign_guide_facts", ["source_proposal_id"])


def downgrade() -> None:
    op.drop_table("campaign_guide_facts")

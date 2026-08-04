"""Add editable session chronicle entries."""

import sqlalchemy as sa
from alembic import op

revision = "0013_session_chronicle"
down_revision = "0012_analysis_lanes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chronicle_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_proposal_id", sa.Uuid(), sa.ForeignKey("analysis_proposals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("section", sa.String(length=30), nullable=False),
        sa.Column("entry_type", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("visibility", sa.String(length=20), nullable=False, server_default="gm"),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "source_proposal_id", name="uq_chronicle_source_proposal"),
    )
    op.create_index("ix_chronicle_entries_session_id", "chronicle_entries", ["session_id"])
    op.create_index("ix_chronicle_entries_source_proposal_id", "chronicle_entries", ["source_proposal_id"])
    op.create_index("ix_chronicle_entries_session_section", "chronicle_entries", ["session_id", "section", "position"])


def downgrade() -> None:
    op.drop_index("ix_chronicle_entries_session_section", table_name="chronicle_entries")
    op.drop_index("ix_chronicle_entries_source_proposal_id", table_name="chronicle_entries")
    op.drop_index("ix_chronicle_entries_session_id", table_name="chronicle_entries")
    op.drop_table("chronicle_entries")

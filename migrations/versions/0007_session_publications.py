"""Add versioned player-facing publication drafts."""

import sqlalchemy as sa
from alembic import op

revision = "0007_session_publications"
down_revision = "0006_analysis_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_publications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("target_path", sa.String(length=500), nullable=False),
        sa.Column("source_proposal_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_published_blob_hash", sa.String(length=64), nullable=True),
        sa.Column("published_commit", sa.String(length=64), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("published_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["game_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["published_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "revision", name="uq_session_publication_revision"),
    )
    op.create_index("ix_session_publications_session_id", "session_publications", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_session_publications_session_id", table_name="session_publications")
    op.drop_table("session_publications")

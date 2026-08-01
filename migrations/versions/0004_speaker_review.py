"""Add campaign speaker profiles and reviewed clips."""

import sqlalchemy as sa
from alembic import op

revision = "0004_speaker_review"
down_revision = "0003_campaign_guide"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "speaker_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "display_name", name="uq_speaker_profile_name"),
    )
    op.create_index("ix_speaker_profiles_campaign_id", "speaker_profiles", ["campaign_id"])
    op.create_table(
        "speaker_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("cluster_label", sa.String(length=80), nullable=False),
        sa.Column("start_seconds", sa.Integer(), nullable=False),
        sa.Column("end_seconds", sa.Integer(), nullable=False),
        sa.Column("speaker_profile_id", sa.Uuid(), nullable=True),
        sa.Column("disposition", sa.String(length=20), nullable=False),
        sa.Column("approved_reference", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("reviewed_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["game_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["speaker_profile_id"], ["speaker_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "cluster_label", "start_seconds", name="uq_speaker_review_clip"),
    )
    op.create_index("ix_speaker_reviews_session_id", "speaker_reviews", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_speaker_reviews_session_id", table_name="speaker_reviews")
    op.drop_table("speaker_reviews")
    op.drop_index("ix_speaker_profiles_campaign_id", table_name="speaker_profiles")
    op.drop_table("speaker_profiles")

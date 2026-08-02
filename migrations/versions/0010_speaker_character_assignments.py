"""Link campaign speakers to player characters."""

import sqlalchemy as sa
from alembic import op

revision = "0010_speaker_characters"
down_revision = "0009_campaign_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "speaker_character_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("speaker_profile_id", sa.Uuid(), nullable=False),
        sa.Column("guide_entry_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["speaker_profile_id"], ["speaker_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["guide_entry_id"], ["campaign_guide_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["game_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "speaker_profile_id", "guide_entry_id", "session_id",
            name="uq_speaker_character_scope",
        ),
    )
    op.create_index(
        "ix_speaker_character_assignments_speaker",
        "speaker_character_assignments", ["speaker_profile_id"],
    )
    op.create_index(
        "ix_speaker_character_assignments_session",
        "speaker_character_assignments", ["session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_speaker_character_assignments_session", table_name="speaker_character_assignments")
    op.drop_index("ix_speaker_character_assignments_speaker", table_name="speaker_character_assignments")
    op.drop_table("speaker_character_assignments")

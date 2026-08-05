"""Store reusable voice centroids so speaker identity carries across sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0017_speaker_voiceprints"
down_revision = "0016_queue_positions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "speaker_voiceprints",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "speaker_profile_id",
            sa.Uuid(),
            sa.ForeignKey("speaker_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("embedding_model", sa.String(length=160), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sample_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("source_session_ids", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "speaker_profile_id", "embedding_model", name="uq_speaker_voiceprint_model"
        ),
    )
    op.create_index(
        "ix_speaker_voiceprints_speaker_profile_id",
        "speaker_voiceprints",
        ["speaker_profile_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_speaker_voiceprints_speaker_profile_id", table_name="speaker_voiceprints"
    )
    op.drop_table("speaker_voiceprints")

"""Let a transcript or diarization be replaced without destroying the previous one.

Re-transcribing or re-diarizing a session used to be impossible (diarization refused
outright once an artifact existed) or would leave two live transcripts with nothing
to say which one counts. Superseding is recorded rather than deleting, so a worse
re-run can be undone.

Speaker reviews describe the clusters of one diarization. A second diarization
renumbers them, so a review kept from the previous generation can silently point at
a different human. Tying each review to the diarization artifact it describes is
what keeps that from happening, and keeps the reviews if the older generation is
restored.
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_artifact_generations"
down_revision = "0019_guide_entity_kinds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "artifacts", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_artifacts_session_kind_live",
        "artifacts",
        ["session_id", "kind", "superseded_at"],
    )
    op.add_column(
        "speaker_reviews",
        sa.Column(
            "diarization_artifact_id",
            sa.Uuid(),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_speaker_reviews_diarization", "speaker_reviews", ["diarization_artifact_id"]
    )

    # Existing reviews describe the newest diarization of their session, which is
    # the only one that existed: a second run was refused before this migration.
    op.execute(sa.text("""
        UPDATE speaker_reviews r SET diarization_artifact_id = newest.id
        FROM (
            SELECT DISTINCT ON (session_id) id, session_id
            FROM artifacts WHERE kind = 'diarization'
            ORDER BY session_id, created_at DESC
        ) AS newest
        WHERE newest.session_id = r.session_id
    """))

    # Where a session somehow holds more than one artifact of a generational kind,
    # everything but the newest is already dead weight; say so explicitly.
    op.execute(sa.text("""
        UPDATE artifacts a SET superseded_at = a.created_at
        WHERE a.kind IN ('raw_transcript', 'normalized_audio', 'diarization')
          AND EXISTS (
              SELECT 1 FROM artifacts newer
              WHERE newer.session_id = a.session_id
                AND newer.kind = a.kind
                AND newer.created_at > a.created_at
          )
    """))


def downgrade() -> None:
    op.drop_index("ix_speaker_reviews_diarization", table_name="speaker_reviews")
    op.drop_column("speaker_reviews", "diarization_artifact_id")
    op.drop_index("ix_artifacts_session_kind_live", table_name="artifacts")
    op.drop_column("artifacts", "superseded_at")

"""Make each analysis a first-class run so generations of findings stay separable."""

import sqlalchemy as sa
from alembic import op

revision = "0018_analysis_runs"
down_revision = "0017_speaker_voiceprints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_artifact_id",
            sa.Uuid(),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("finding_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_analysis_runs_session_id", "analysis_runs", ["session_id"])
    op.create_index("ix_analysis_runs_session", "analysis_runs", ["session_id", "created_at"])

    op.add_column(
        "analysis_proposals",
        sa.Column(
            "analysis_run_id",
            sa.Uuid(),
            sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_analysis_proposals_analysis_run_id", "analysis_proposals", ["analysis_run_id"]
    )
    op.add_column(
        "game_sessions",
        sa.Column(
            "active_analysis_run_id",
            sa.Uuid(),
            sa.ForeignKey("analysis_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "chronicle_entries", sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True)
    )

    # Existing findings already record their job and source artifact in run_metadata,
    # so historical generations get correct lineage instead of being orphaned.
    op.execute(sa.text("""
        INSERT INTO analysis_runs (
            id, session_id, source_artifact_id, job_id, provider, model, status,
            finding_count, notes, created_at, completed_at
        )
        SELECT
            gen_random_uuid(),
            grouped.session_id,
            grouped.source_artifact_id,
            grouped.job_id,
            grouped.provider,
            grouped.model,
            'succeeded',
            grouped.finding_count,
            'backfilled from finding metadata',
            grouped.created_at,
            grouped.created_at
        FROM (
            SELECT
                p.session_id,
                NULLIF(p.run_metadata->>'source_artifact_id', '')::uuid AS source_artifact_id,
                NULLIF(p.run_metadata->>'job_id', '')::uuid AS job_id,
                MIN(p.provider) AS provider,
                MIN(p.model) AS model,
                COUNT(*) AS finding_count,
                MIN(p.created_at) AS created_at
            FROM analysis_proposals p
            GROUP BY 1, 2, 3
        ) AS grouped
    """))

    op.execute(sa.text("""
        UPDATE analysis_proposals p SET analysis_run_id = r.id
        FROM analysis_runs r
        WHERE r.session_id = p.session_id
          AND r.job_id IS NOT DISTINCT FROM NULLIF(p.run_metadata->>'job_id', '')::uuid
          AND r.source_artifact_id IS NOT DISTINCT FROM
              NULLIF(p.run_metadata->>'source_artifact_id', '')::uuid
    """))

    # The newest run per session becomes the active one, so existing sessions keep
    # showing their most recent analysis without anyone choosing.
    op.execute(sa.text("""
        UPDATE game_sessions s SET active_analysis_run_id = newest.id
        FROM (
            SELECT DISTINCT ON (session_id) id, session_id
            FROM analysis_runs ORDER BY session_id, created_at DESC
        ) AS newest
        WHERE newest.session_id = s.id
    """))


def downgrade() -> None:
    op.drop_column("chronicle_entries", "edited_at")
    op.drop_column("game_sessions", "active_analysis_run_id")
    op.drop_index("ix_analysis_proposals_analysis_run_id", table_name="analysis_proposals")
    op.drop_column("analysis_proposals", "analysis_run_id")
    op.drop_index("ix_analysis_runs_session", table_name="analysis_runs")
    op.drop_index("ix_analysis_runs_session_id", table_name="analysis_runs")
    op.drop_table("analysis_runs")

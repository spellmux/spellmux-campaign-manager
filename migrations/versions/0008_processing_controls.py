"""Add durable queue priority, cancellation, and processing pause controls."""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0008_processing_controls"
down_revision = "0007_session_publications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs", sa.Column("priority", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "jobs", sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.create_table(
        "processing_controls",
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("kind"),
    )
    controls = sa.table(
        "processing_controls",
        sa.column("kind", sa.String()),
        sa.column("paused", sa.Boolean()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        controls,
        [
            {"kind": kind, "paused": False, "updated_at": datetime.now(UTC)}
            for kind in (
                "transcription", "diarization", "analysis", "image_generation",
                "__compute_lane__",
            )
        ],
    )


def downgrade() -> None:
    op.drop_table("processing_controls")
    op.drop_column("jobs", "cancel_requested")
    op.drop_column("jobs", "priority")

"""Add explicit ordering within each priority band."""

import sqlalchemy as sa
from alembic import op

revision = "0016_queue_positions"
down_revision = "0015_backfill_guide_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("queue_position", sa.Integer(), nullable=False, server_default="0"))
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY priority ORDER BY created_at, id) - 1 AS position
            FROM jobs WHERE status IN ('queued', 'running')
        )
        UPDATE jobs SET queue_position = ranked.position
        FROM ranked WHERE jobs.id = ranked.id
    """))
    op.create_index("ix_jobs_priority_position", "jobs", ["status", "priority", "queue_position"])


def downgrade() -> None:
    op.drop_index("ix_jobs_priority_position", table_name="jobs")
    op.drop_column("jobs", "queue_position")

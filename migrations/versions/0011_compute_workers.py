"""Add portable compute worker endpoints."""

import sqlalchemy as sa
from alembic import op

revision = "0011_compute_workers"
down_revision = "0010_speaker_characters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "compute_workers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False, server_default="ollama"),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("analysis_model", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("concurrency", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_status", sa.String(length=30), nullable=False, server_default="unknown"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("available_models", sa.JSON(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("compute_workers")

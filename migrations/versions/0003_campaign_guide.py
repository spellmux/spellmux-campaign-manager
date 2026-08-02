"""Add typed campaign coaching and canonical vocabulary."""

import sqlalchemy as sa
from alembic import op

revision = "0003_campaign_guide"
down_revision = "0002_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_guide_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("canonical_name", sa.String(length=200), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "campaign_id",
            "kind",
            "canonical_name",
            name="uq_campaign_guide_kind_name",
        ),
    )
    op.create_index("ix_campaign_guide_entries_campaign_id", "campaign_guide_entries", ["campaign_id"])


def downgrade() -> None:
    op.drop_index("ix_campaign_guide_entries_campaign_id", table_name="campaign_guide_entries")
    op.drop_table("campaign_guide_entries")

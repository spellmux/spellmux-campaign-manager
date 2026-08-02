"""Add portable campaign play and tool settings."""

import sqlalchemy as sa
from alembic import op

revision = "0009_campaign_settings"
down_revision = "0008_processing_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaigns", sa.Column("game_system", sa.String(length=120), nullable=False, server_default="")
    )
    op.add_column(
        "campaigns", sa.Column("play_mode", sa.String(length=40), nullable=False, server_default="")
    )
    op.add_column(
        "campaigns", sa.Column("vtt", sa.String(length=160), nullable=False, server_default="")
    )
    op.add_column(
        "campaigns",
        sa.Column("character_source", sa.String(length=160), nullable=False, server_default=""),
    )
    op.add_column(
        "campaigns", sa.Column("notes", sa.Text(), nullable=False, server_default="")
    )


def downgrade() -> None:
    op.drop_column("campaigns", "notes")
    op.drop_column("campaigns", "character_source")
    op.drop_column("campaigns", "vtt")
    op.drop_column("campaigns", "play_mode")
    op.drop_column("campaigns", "game_system")

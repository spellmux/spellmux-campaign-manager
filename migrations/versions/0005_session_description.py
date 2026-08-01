"""Add editable session descriptions."""

import sqlalchemy as sa
from alembic import op

revision = "0005_session_description"
down_revision = "0004_speaker_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "game_sessions",
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("game_sessions", "description")

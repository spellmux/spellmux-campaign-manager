"""Backfill structured facts from previously approved guide promotions."""

import sqlalchemy as sa
from alembic import op

revision = "0015_backfill_guide_facts"
down_revision = "0014_guide_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        INSERT INTO campaign_guide_facts
            (id, guide_entry_id, session_id, source_proposal_id, category, value,
             status, confidence, visibility, created_by_id, created_at, updated_at)
        SELECT
            gen_random_uuid(), p.promoted_guide_entry_id, p.session_id, p.id,
            'session_detail', p.body, 'canonical', p.confidence, p.visibility,
            COALESCE(p.reviewed_by_id, p.created_by_id), p.created_at, p.updated_at
        FROM analysis_proposals p
        WHERE p.status = 'approved'
          AND p.promoted_guide_entry_id IS NOT NULL
          AND length(trim(p.body)) > 0
          AND NOT EXISTS (
              SELECT 1 FROM campaign_guide_facts f
              WHERE f.source_proposal_id = p.id
          )
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DELETE FROM campaign_guide_facts
        WHERE source_proposal_id IN (
            SELECT id FROM analysis_proposals WHERE status = 'approved'
        )
    """))

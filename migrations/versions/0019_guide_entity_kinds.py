"""Reduce the campaign guide to reusable entities and remove ambiguous kinds.

"character" only ever meant "a person, unclassified"; analysis always resolved it
to npc, but an OtterWiki import wrote it directly and left ambiguous entries
behind. Guide lookup keys on kind and name, so the same entity filed under
several kinds defeats the alias resolution the pipeline depends on: one entity
existed three times over.
"""

import sqlalchemy as sa
from alembic import op

revision = "0019_guide_entity_kinds"
down_revision = "0018_analysis_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fold each "character" entry into the npc of the same name where one exists,
    # keeping the union of aliases and whichever notes are longer.
    op.execute(sa.text("""
        UPDATE campaign_guide_entries AS npc
        SET aliases = (
                SELECT COALESCE(jsonb_agg(DISTINCT value)::json, '[]'::json)
                FROM (
                    SELECT jsonb_array_elements_text(npc.aliases::jsonb) AS value
                    UNION
                    SELECT jsonb_array_elements_text(ch.aliases::jsonb) AS value
                ) AS merged
            ),
            notes = CASE
                WHEN length(ch.notes) > length(npc.notes) THEN ch.notes ELSE npc.notes
            END,
            updated_at = now()
        FROM campaign_guide_entries AS ch
        WHERE ch.kind = 'character'
          AND npc.kind = 'npc'
          AND npc.campaign_id = ch.campaign_id
          AND lower(npc.canonical_name) = lower(ch.canonical_name)
    """))

    # Facts and assignments that pointed at the duplicate move to the survivor.
    op.execute(sa.text("""
        UPDATE campaign_guide_facts f SET guide_entry_id = npc.id
        FROM campaign_guide_entries ch, campaign_guide_entries npc
        WHERE f.guide_entry_id = ch.id
          AND ch.kind = 'character' AND npc.kind = 'npc'
          AND npc.campaign_id = ch.campaign_id
          AND lower(npc.canonical_name) = lower(ch.canonical_name)
    """))
    op.execute(sa.text("""
        UPDATE speaker_character_assignments a SET guide_entry_id = npc.id
        FROM campaign_guide_entries ch, campaign_guide_entries npc
        WHERE a.guide_entry_id = ch.id
          AND ch.kind = 'character' AND npc.kind = 'npc'
          AND npc.campaign_id = ch.campaign_id
          AND lower(npc.canonical_name) = lower(ch.canonical_name)
    """))
    op.execute(sa.text("""
        DELETE FROM campaign_guide_entries ch
        WHERE ch.kind = 'character'
          AND EXISTS (
              SELECT 1 FROM campaign_guide_entries npc
              WHERE npc.kind = 'npc' AND npc.campaign_id = ch.campaign_id
                AND lower(npc.canonical_name) = lower(ch.canonical_name)
          )
    """))

    # Whatever remains was simply misfiled: an unclassified person is an NPC.
    op.execute(sa.text(
        "UPDATE campaign_guide_entries SET kind = 'npc', updated_at = now() "
        "WHERE kind = 'character'"
    ))

    # A named individual is an npc whatever its species; a creature entry is a
    # type, which is what D&D calls a monster. So a creature sharing a name with
    # an npc is the same individual filed twice: "The White Rabbit" is a person,
    # while "Mock Turtle" is a type and stays a creature.
    op.execute(sa.text("""
        UPDATE campaign_guide_entries AS npc
        SET aliases = (
                SELECT COALESCE(jsonb_agg(DISTINCT value)::json, '[]'::json)
                FROM (
                    SELECT jsonb_array_elements_text(npc.aliases::jsonb) AS value
                    UNION
                    SELECT jsonb_array_elements_text(cr.aliases::jsonb) AS value
                ) AS merged
            ),
            notes = CASE
                WHEN length(cr.notes) > length(npc.notes) THEN cr.notes ELSE npc.notes
            END,
            updated_at = now()
        FROM campaign_guide_entries AS cr
        WHERE cr.kind = 'creature' AND npc.kind = 'npc'
          AND npc.campaign_id = cr.campaign_id
          AND lower(npc.canonical_name) = lower(cr.canonical_name)
    """))
    op.execute(sa.text("""
        UPDATE campaign_guide_facts f SET guide_entry_id = npc.id
        FROM campaign_guide_entries cr, campaign_guide_entries npc
        WHERE f.guide_entry_id = cr.id
          AND cr.kind = 'creature' AND npc.kind = 'npc'
          AND npc.campaign_id = cr.campaign_id
          AND lower(npc.canonical_name) = lower(cr.canonical_name)
    """))
    op.execute(sa.text("""
        UPDATE speaker_character_assignments a SET guide_entry_id = npc.id
        FROM campaign_guide_entries cr, campaign_guide_entries npc
        WHERE a.guide_entry_id = cr.id
          AND cr.kind = 'creature' AND npc.kind = 'npc'
          AND npc.campaign_id = cr.campaign_id
          AND lower(npc.canonical_name) = lower(cr.canonical_name)
    """))
    op.execute(sa.text("""
        DELETE FROM campaign_guide_entries cr
        WHERE cr.kind = 'creature'
          AND EXISTS (
              SELECT 1 FROM campaign_guide_entries npc
              WHERE npc.kind = 'npc' AND npc.campaign_id = cr.campaign_id
                AND lower(npc.canonical_name) = lower(cr.canonical_name)
          )
    """))

    # "monster" was "creature" under another name.
    op.execute(sa.text(
        "UPDATE campaign_guide_entries SET kind = 'creature', updated_at = now() "
        "WHERE kind = 'monster'"
    ))

    # Spells are not worth a dictionary entry; their facts go with them.
    op.execute(sa.text(
        "DELETE FROM campaign_guide_facts WHERE guide_entry_id IN "
        "(SELECT id FROM campaign_guide_entries WHERE kind = 'spell')"
    ))
    op.execute(sa.text("DELETE FROM campaign_guide_entries WHERE kind = 'spell'"))


def downgrade() -> None:
    # The original kind of a merged entry is not recoverable, so this only stops
    # the migration from being a one-way door in the schema sense.
    pass

# Planning Studio roadmap

The Planning Studio is a future GM-only workspace for turning established campaign context into
possible encounters, story beats, locations, NPCs, rewards, and session plans. Planning material
is hypothetical by default: it must never silently become campaign canon or appear to players.

## Design principles

- Keep canon, private planning, model suggestions, and player-visible material visibly distinct.
- Show which campaign facts influenced every generated suggestion.
- Let the GM select, exclude, or pin context instead of sending the entire campaign blindly.
- Treat generated ideas as editable drafts, never authoritative facts.
- Preserve alternate ideas without polluting retrieval for completed sessions.
- Allow planning without a language model through templates, tables, and deterministic generators.
- Make promotion explicit: a planned entity or fact becomes canonical only through GM approval.

## Planning context

The retrieval layer should assemble a compact planning packet from approved material:

- Campaign Guide entries and campaign coaching;
- PCs, players, goals, relationships, and current conditions;
- recent session recaps and important moments;
- unresolved story threads, promises, mysteries, and known consequences;
- factions, locations, NPCs, monsters, items, spells, and rules system;
- prior encounters and recently used motifs, enemies, rewards, and names;
- Foundry data selected through the connector;
- GM-pinned source passages, notes, PDFs, and private secrets;
- campaign tone, safety tools, desired difficulty, and available session time.

Every generated planning artifact should retain citations to the context entries that informed it.
The GM should be able to mark a suggestion as supported by canon, a deliberate extrapolation, or
an unconstrained creative option.

## Planning workflow

1. Choose a campaign and optionally a target session or story arc.
2. Select a planning mode such as brainstorm, encounter, story beat, NPC, location, reward, or
   full session outline.
3. Review the automatically retrieved context; pin, exclude, or add sources.
4. Enter constraints: tone, duration, party level, location, desired characters, difficulty,
   secrets, themes, and content to avoid.
5. Generate several compact options before expanding one.
6. Compare options by purpose, campaign connections, likely consequences, and preparation cost.
7. Edit or combine selected ideas in a structured planning document.
8. Export preparation material to Foundry or a private GM page when appropriate.
9. After play, reconcile planned material with what actually happened. Only approved facts are
   promoted into canon.

## Planning artifact types

- **Story thread:** premise, status, participants, evidence, open questions, and possible outcomes.
- **Story beat:** purpose, trigger, reveal, involved entities, alternatives, and consequences.
- **Encounter:** objective, opposition, environment, escalation, clues, rewards, and exits.
- **Session outline:** opening, likely scenes, flexible beats, clocks, secrets, and fallback content.
- **NPC draft:** role, desire, fear, mannerism, voice cue, relationships, and visual brief.
- **Location draft:** function, sensory details, inhabitants, discoveries, hazards, and exits.
- **Reward draft:** narrative purpose, mechanics source, owner, risks, and visual brief.
- **Rumor or secret:** truth status, who knows it, delivery options, and consequences.

System-aware encounter planning should begin with editable guidance rather than pretending to be
a complete rules engine. Connectors may later read party and creature data from Foundry, calculate
system-specific estimates, and export actors, journals, roll tables, scenes, or encounter folders.

## Creative modes

Planning requests should expose a creativity control rather than relying on a hidden model setting:

- **Canon-tight:** use only established entities and conservative consequences.
- **Connected:** introduce new material that directly develops existing threads.
- **Surprising:** make less obvious connections while respecting campaign constraints.
- **Wild cards:** offer deliberately speculative ideas, clearly labeled as such.

The model should normally generate short option cards first. Expanding only selected cards reduces
inference cost, review burden, and the tendency to commit prematurely to one long plan.

## Name generator

The name generator should be available anywhere an entity is created and as a standalone Planning
Studio tool. It should not require a language model for its normal operation.

### Inputs

- entity type: person, family, faction, settlement, tavern, ship, item, spell, creature, or other;
- campaign and optional culture, ancestry, language, region, faction, or naming tradition;
- tone and phonetic guidance;
- desired count, length, prefixes/suffixes, and prohibited sounds;
- whether existing names should be imitated loosely or avoided entirely.

### Generation methods

1. Deterministic syllable and phonotactic templates supplied by the project or campaign.
2. User-owned name lists and weighted fragments.
3. Optional local-model suggestions informed by campaign context.
4. Hybrid generation, where deterministic candidates are ranked or lightly refined by a model.

Generated candidates should include pronunciation, optional meaning or etymology, tags, and the
seed/method used. Campaign-specific naming profiles should be editable and exportable. Bundled
name data must have a license compatible with the project; copyrighted setting lists should not
be copied into the distribution.

### Name lifecycle

- Detect exact and phonetically similar collisions with existing campaign names.
- Mark candidates as available, rejected, reserved, used, or retired.
- Remember rejected names so repeated generations do not immediately return them.
- Reserve a name without creating a canonical entity.
- Promote a selected name directly into a new PC, NPC, monster, location, faction, or item draft.
- Preserve aliases, pronunciation, and source information when the draft becomes canonical.

## Suggested delivery order

1. Add private planning artifacts and explicit canon/publish boundaries.
2. Add story-thread tracking and context selection with citations.
3. Add the deterministic name generator and campaign naming profiles.
4. Add compact context-aware brainstorming cards.
5. Add structured story-beat, encounter, and session-outline editors.
6. Reconcile plans against completed session analysis.
7. Add Foundry import/export and system-specific encounter assistance.

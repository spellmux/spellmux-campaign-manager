# ADR 0003: Campaign Guide and reviewed speaker identity

## Status

Accepted

## Decision

Campaign-specific knowledge is stored as typed, visibility-aware Campaign Guide
entries. Canonical names, aliases, pronunciations, characters, locations, items,
spells, factions, quests, rules, and GM instructions are inputs to transcription
and analysis providers. Model output proposes additions or changes; it does not
silently modify approved campaign knowledge.

Diarization and speaker identity are separate stages. Diarization creates anonymous
voice clusters. The review interface presents several short representative clips
per cluster and permits a GM to:

- assign or correct a person;
- merge clusters belonging to the same person;
- mark crosstalk, noise, or uncertainty;
- approve selected clips as future identification references.

Optional speaker profiles and derived embeddings are campaign-scoped, local,
GM-only, deletable, and never included in wiki publication or player exports.
Low-confidence identity matches remain suggestions requiring review.

## Consequences

- Transcription vocabulary can improve before an LLM analyzes the session.
- Canonical campaign facts remain human-governed.
- Speaker validation becomes an explicit prerequisite for a reviewed transcript.
- Voice samples and embeddings receive the same protection as private audio.


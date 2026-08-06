# The analysis pipeline contract

What each stage of analysis reads, what it must produce, how the result is judged, and what happens
when it fails. This document exists because "analysis failed" was, for a long time, the only thing
the system could tell you: a single unusable model response could lose an entire session's work, and
nobody could say which stage had gone wrong or what the stage was supposed to look like.

## The shape of the work

```text
recording -> transcription -> diarization -> speaker review -> analysis run -> review queue
          -> Chronicle (canon) -> publication draft -> player page
```

Everything left of the review queue is machine output and immutable. Everything right of it is
human judgment. The Chronicle is the boundary: once a person edits a Chronicle entry it becomes the
campaign's canon and no later machine output overwrites it.

## Analysis runs are generations

One session may be transcribed and analysed many times. Each analysis is an `analysis_run` row, and
every finding it produces carries that run's id.

- A session has one **active run**. Only its findings reach the review queue, the campaign inbox,
  approval, and publication drafts. Superseded findings stay in the database so a disappointing
  re-analysis can be undone rather than mourned.
- The run becomes active **when it succeeds**, not when it starts. Switching earlier would empty the
  review queue for the duration of a re-analysis and leave nothing reviewable at all if the new run
  failed.
- A run that produces no findings is marked `failed` and never becomes active.
- A run still marked `running` when the next one starts is marked `interrupted`: it was killed by a
  restart, and saying so is more useful than leaving it looking live forever.
- Findings authored by hand have **no** run and belong to every generation.
- `POST .../analysis-runs/{id}/activate` switches generations. Selecting a run also selects the
  transcript it read, because the transcript is the run's input.

A run's checkpoints belong to the run. Re-analysis replaces only its own findings, which is what
lets a new generation build while the GM is still reviewing the previous one.

## Transcripts and diarizations are generations too

`raw_transcript`, `normalized_audio`, and `diarization` are **generational kinds**: a session holds
one live copy of each, and re-running the stage replaces it.

- `POST .../transcription` re-transcribes the session's existing audio. `POST .../diarization`
  re-diarizes it. Neither requires re-uploading the recording, and both are refused while a
  transcription, diarization, or analysis job for that session is queued or running.
- The old artifact is marked `superseded_at` **when the replacement lands**, not when the job is
  queued, so a failed re-run leaves the previous result in place. Nothing downstream reads a
  superseded artifact; it stays on disk and in the API listing so a worse re-run can be undone.
- Speaker reviews belong to the diarization whose clusters they describe. A second diarization
  renumbers the clusters, so a review carried over from the previous generation would attribute
  lines to whoever now holds that label. Retired reviews are kept, not deleted: restoring the earlier
  diarization restores the review work with it.

## Extraction passes

Extraction reads the transcript in overlapping chunks of `CAMPAIGN_ANALYSIS_CHUNK_CHARS`
characters and asks for at most 8 source-supported candidates per chunk.

| | |
|---|---|
| **Input** | Speaker-attributed transcript segments, the Campaign Guide, and the speaker-to-character mapping. Real player names are deliberately withheld. |
| **Output** | Candidate findings with 1-3 evidence segment ids and a short exact quote each. |
| **Acceptance** | The response parses and yields at least one finding with resolvable evidence. |
| **On failure** | The chunk is recorded in `chunk_failures` and the job continues. A job fails only when *every* chunk fails. Findings are checkpointed after each successful chunk, so a crash costs one chunk rather than the session. |

Grammar-constrained decoding, not prose instruction, enforces the response shape: a section told in
prose to return only a recap returned scenes and entities and no recap, and no amount of post-hoc
filtering recovers what was never sampled.

## Consolidation sections

Consolidation is an editorial pass over the extracted candidates, run once per section. Each
section's sampling grammar allows only its own kinds, so one section cannot crowd out another.

| Section | Kinds | Asked for | Cap |
|---|---|---|---|
| `recap` | `session_summary` | One recap, 5-7 paragraphs, 350-650 words, covering opening, turning points, choices, escalation, and ending | 8 entries, merged into one recap |
| `scenes` | `scene`, `memorable_moment` | 6-10 chronological scenes under 90 words; up to 4 moments under 60 | 14 |
| `entities` | `player_character`, `npc`, `location`, `item`, `creature`, `faction`, `deity` | Reference entries: what the entity is and what the party learned | 14 |
| `threads` | `quest`, `important_decision`, `unresolved_question` | Real objectives, consequential decisions, genuine in-fiction mysteries | 10 |
| `meta` | `rule`, `follow_up`, `table_note` | Durable rulings, explicit follow-ups, scheduling and technical notes | 10 |

Acceptance per section: the parsed result contains at least one entry of a required kind. The recap
is the only required section. Trimming is ranked, not positional — a recap arriving late in the
response used to be discarded by a positional cut while the guard checked the untrimmed list and
reported success.

**On failure**: consolidation is an improvement pass over findings that are already checkpointed.
If it throws, the extracted findings stand and `consolidation_error` records why, because losing a
whole run because the editor would not produce a recap wastes every chunk that did succeed.

## Entity kind rules

The Guide is a dictionary of reusable entities, not a story log. It holds no scenes, moments,
threads, or table notes.

- A **named individual is an `npc`**, whatever its species. "Bob the Mock Turtle" is an npc.
- A **`creature` is a type** — what D&D calls a monster. "Mock Turtle" is a creature.
- `player_character` is a PC, resolved from the speaker-to-character mapping rather than guessed.
- A speaker or player is never their character, and transcript role labels (`GM`,
  `Unidentified speaker`, `Speaker A`) are never entities.

Each entry answers two questions: what is known about this thing, and which sessions it appeared in.
The first accumulates as sourced facts, one per approval. The second is **derived** from those facts'
sessions rather than stored or model-authored, so an entry's encounter history cannot drift from the
findings behind it.

## Review, Chronicle, and canon

- Review is approve or reject. **Model findings are immutable**: what the model produced is the
  record of what the model produced. Hand-authored findings stay editable.
- Approving a guide-kind finding creates or links its Guide entry and writes a sourced fact.
- Approving a finding writes a Chronicle entry. Re-approving the same thing from a later generation
  **refreshes** an untouched entry rather than duplicating it.
- A Chronicle entry a human has edited carries `edited_at` and is **never** overwritten by a later
  run. That is the canon rule: the model's work is immutable, and a human edit in the Chronicle is
  the campaign's truth.
- Publication drafts render only the active run's approved, player-visible findings. Every analysis
  kind is either in the renderer's section map or deliberately withheld, and a test enforces it: the
  map silently outlived a kind rename once, and approved findings stopped reaching player pages with
  nothing to show it had happened.

# Product specification: 0.1

## Purpose

Campaign Manager is a local-first application that turns recorded tabletop RPG
sessions into reviewed transcripts and curated campaign knowledge. A GM controls
what becomes visible to players or is published externally.

## Primary users

- **Administrator:** deploys the application, configures providers, and manages
  instance-wide settings.
- **Game master:** creates campaigns, reviews transcripts, approves generated
  material, and publishes player-safe content.
- **Player:** views only material approved for campaigns they have joined.

## Core workflow

1. A GM uploads an audio recording to a campaign session.
2. A background worker normalizes and transcribes the recording locally.
3. Diarization proposes speaker turns; the GM assigns and corrects identities.
4. A local language model proposes recaps, scenes, and entity changes.
5. All proposals begin as GM-only drafts.
6. The GM approves player-visible content.
7. Approved content appears in the player portal and may be published through a
   configured adapter such as OtterWiki or generic Git/Markdown.

## Privacy invariants

- Original audio, raw transcripts, and model drafts are GM-only by default.
- No artifact is sent to a remote provider unless an administrator explicitly
  configures that provider and the artifact policy permits it.
- Player and public visibility require explicit GM approval.
- Publishing is auditable and repeatable.
- Secrets and provider credentials never appear in exported campaign data or
  diagnostic output.

## 0.1 acceptance criteria

- Runs on a CPU-only Docker host using documented Compose configuration.
- Supports local administrator, GM, and player accounts.
- Creates campaigns and sessions and accepts common audio formats.
- Persists background jobs and survives process restarts.
- Produces a timestamped local transcription.
- Produces proposed speaker turns and permits correction.
- Produces a reviewed transcript artifact.
- Generates structured session-note drafts through a local model provider.
- Separates GM-only and player-approved content.
- Previews exact Markdown changes before publication.
- Publishes approved notes through generic Git/Markdown and OtterWiki presets.
- Provides backup, restore, health, and diagnostic commands.

## Explicitly deferred

- Rules engines and character-sheet automation
- Virtual tabletop functionality
- Real-time recording or transcription
- Tactical map generation guarantees
- Unrestricted host or Unraid administration through MCP


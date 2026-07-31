# Architecture

## Shape

Campaign Manager begins as a modular monolith. One source tree produces an HTTP
server, a background worker, and an administrative CLI. The worker can later run
on a separate machine without changing the campaign API or database.

## System boundaries

- PostgreSQL is authoritative for users, campaigns, entities, permissions, jobs,
  artifact metadata, approvals, and publication records.
- Artifact storage holds audio, transcripts, generated images, and exports.
- Generated Markdown is a projection, never the campaign database.
- Web, CLI, and MCP clients use the versioned HTTP API.
- Provider adapters isolate transcription, diarization, language-model, image,
  storage, and publishing implementations.

## Artifact lineage

Each stage creates an immutable, versioned artifact linked to its inputs and the
provider configuration that created it:

```text
original audio -> normalized audio -> raw transcript
                                      + diarization
                                      -> reviewed transcript
                                      -> analysis proposal
                                      -> approved notes
                                      -> rendered publication
```

Rerunning a stage creates a new artifact; it does not mutate previously approved
or published material.

## Portability

- Containers run without access to the Docker socket.
- CPU processing is the baseline; accelerators are optional worker capabilities.
- Filesystem storage is the initial implementation; S3-compatible storage can be
  added behind the same interface.
- OtterWiki is a publisher adapter, not a runtime dependency.
- Unraid-specific deployment consists of defaults, documentation, and a template.


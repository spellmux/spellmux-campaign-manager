# Campaign Manager

Campaign Manager is a self-hosted workspace for turning tabletop session audio
into reviewed transcripts, curated campaign knowledge, and publishable player
notes.

The project is designed around local-first processing and replaceable providers:

- speech-to-text and speaker diarization;
- campaign-aware analysis;
- image generation;
- Git/Markdown and wiki publishing targets.

OtterWiki is the first supported wiki target, but is not required.

## Current status

This repository is an early `0.1` foundation. It currently provides:

- a versioned HTTP health endpoint;
- a `campaignctl doctor` preflight command;
- separate server and worker process entry points;
- a CPU-first Docker Compose deployment;
- PostgreSQL migrations and persistent job/session models;
- Argon2 password hashing and opaque bearer-token authentication;
- administrator bootstrap and campaign-scoped owner/GM/player roles;
- authenticated campaign creation and listing APIs;
- session creation and private streamed audio ingestion;
- durable, selectively claimed background jobs;
- typed Campaign Guide coaching and canonical vocabulary;
- a minimal authenticated web workspace;
- a preview-first, resumable Unraid installer;
- architecture, privacy, and Unraid deployment decisions.

The worker currently processes only an internal `noop` job type. Audio creates a
durable queued `transcription` job, but the worker deliberately leaves it untouched
until a local speech provider and review workflow are implemented. Publishing is
also intentionally incomplete.

## Local commands

```bash
python -m campaign_manager.cli doctor
python -m campaign_manager.api
python -m campaign_manager.worker
```

Install the development dependencies first:

```bash
python -m pip install -e ".[dev]"
pytest
```

## Docker

Copy `.env.example` to `.env`, replace the database password, and run:

```bash
docker compose up --build
```

The web API is then available at `http://localhost:8088/api/v1/health`.

## Documentation

- [Product specification](docs/product-spec.md)
- [Architecture](docs/architecture.md)
- [Unraid deployment](deploy/unraid/README.md)

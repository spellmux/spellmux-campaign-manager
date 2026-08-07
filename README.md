# Spellmux Campaign Manager

Spellmux Campaign Manager is a self-hosted, local-first workspace that turns tabletop
session audio, transcripts, and notes into reviewed campaign knowledge and curated
player recaps.

> [!IMPORTANT]
> This project is under active development. Back up your database and artifacts before
> upgrading, and review generated material before publishing it to players.

## What it does

- organizes campaigns, sessions, source audio, transcripts, and notes;
- transcribes audio locally with faster-whisper;
- performs optional local speaker diarization with pyannote.audio;
- lets a GM validate speaker clusters and maintain campaign-wide speaker identities;
- analyzes sessions locally with Ollama and produces evidence-backed review proposals;
- routes analysis to administrator-managed compute workers with priority and fallback;
- separates GM-only findings from player-visible material;
- maintains a canonical Campaign Guide for names, locations, characters, items, and lore;
- generates versioned player-session drafts and publishes approved Markdown to OtterWiki;
- serializes heavy jobs and provides pause, priority, cancellation, and game-session controls;
- supports owner, GM, and player accounts with campaign-scoped permissions.

Audio, transcripts, models, and campaign data remain on infrastructure you control. External
model providers are not required. OtterWiki is an optional publishing adapter rather than a
runtime dependency.

## Current architecture

The application uses FastAPI, PostgreSQL, and separate background workers. Optional workers
provide faster-whisper transcription, pyannote diarization, and Ollama analysis. Docker Compose
is the portable reference deployment; Unraid is a supported deployment target.

Instance administrators can register Ollama-compatible endpoints under **Compute Workers**.
Endpoints may run in the bundled Docker network or on another LAN machine; URLs, models,
capabilities, and routing priority are stored as deployment configuration rather than source
code. The bundled Ollama service remains the fallback when no managed analysis worker is ready.
Ollama endpoints do not authenticate local API calls, so restrict them with host firewalls and
never publish their ports through an internet-facing reverse proxy.

See [the architecture guide](docs/architecture.md), [product specification](docs/product-spec.md),
[remote compute layout](docs/remote-compute.md),
[local models and hardware roadmap](docs/models-and-hardware.md),
[Planning Studio roadmap](docs/planning-studio.md),
[processing-workflow roadmap](docs/processing-workflows.md), and
[Unraid deployment notes](deploy/unraid/README.md) for more detail.

## Quick start with Docker Compose

Requirements:

- Docker Engine with Compose;
- enough storage for uploaded audio and local models;
- a Hugging Face token with accepted pyannote model terms if diarization is enabled.

Copy the example configuration and replace its placeholder database credentials:

```bash
cp .env.example .env
docker compose up --build
```

The application is then available at `http://localhost:8088`. Create the first administrator
inside the server container:

```bash
docker compose exec server campaignctl create-admin
```

Some models are large and their first download can take time. Disable optional providers in
`.env` when you only need partial workflows such as pasted transcripts or notes.

## Development

Python 3.11 or newer is required.

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
```

Frontend changes require Node 22 or newer. Build the typed Preact assets into the Python
package before running the server from a source checkout:

```bash
cd frontend
npm ci
npm run check
npm run build:package
```

Docker performs this frontend build automatically; Node is not installed in the final runtime
image.

Useful local entry points are `campaign-server`, `campaign-worker`, and `campaignctl doctor`.

## Project status and roadmap

The current `0.1` series is a functional foundation rather than a stable release. Planned work
includes a clearer campaign-centered navigation system, image-generation review, Campaign So
Far rollups, PDF ingestion, Foundry VTT integration, and portable provider configuration.

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) and
[SECURITY.md](SECURITY.md).

## License

Copyright (C) 2026 Spellmux contributors.

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE). If you run
a modified version as a network service, the AGPL requires that you offer its corresponding
source code to users of that service.

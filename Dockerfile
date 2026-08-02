FROM node:22-alpine AS frontend

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run check && npm run build

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CAMPAIGN_HOST=0.0.0.0 \
    CAMPAIGN_ARTIFACT_ROOT=/data/artifacts \
    CAMPAIGN_PUBLISH_ROOT=/data/publish

RUN useradd --uid 99 --gid 100 --create-home campaign

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=frontend /frontend/dist ./src/campaign_manager/static/assets
COPY alembic.ini ./
COPY migrations ./migrations
RUN python -m pip install --no-cache-dir .

RUN mkdir -p /data/artifacts /data/publish \
    && chown -R 99:100 /data

EXPOSE 8088

FROM base AS test
USER root
RUN python -m pip install --no-cache-dir ".[dev]"
COPY tests ./tests
CMD ["pytest", "-q"]

FROM base AS runtime
USER 99:100
CMD ["campaign-server"]

FROM base AS worker
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir ".[transcription]"
USER 99:100
CMD ["campaign-worker"]

FROM base AS diarization-worker
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install --no-cache-dir ".[diarization]"
USER 99:100
CMD ["campaign-worker"]

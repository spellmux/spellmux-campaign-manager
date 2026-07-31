FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CAMPAIGN_HOST=0.0.0.0 \
    CAMPAIGN_ARTIFACT_ROOT=/data/artifacts \
    CAMPAIGN_PUBLISH_ROOT=/data/publish

RUN useradd --uid 99 --gid 100 --create-home campaign

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
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

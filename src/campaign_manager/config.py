"""Environment-backed application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    host: str
    port: int
    database_url: str
    artifact_root: Path
    publish_root: Path
    model_root: Path
    max_upload_bytes: int
    worker_poll_seconds: int
    transcription_provider: str
    whisper_model: str
    whisper_device: str
    whisper_compute_type: str
    whisper_cpu_threads: int
    log_level: str

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            environment=os.getenv("CAMPAIGN_ENV", "development"),
            host=os.getenv("CAMPAIGN_HOST", "127.0.0.1"),
            port=_integer_environment("CAMPAIGN_PORT", 8088),
            database_url=os.getenv(
                "CAMPAIGN_DATABASE_URL",
                "postgresql+psycopg://campaign:campaign@localhost:5432/campaign",
            ),
            artifact_root=Path(os.getenv("CAMPAIGN_ARTIFACT_ROOT", "./data/artifacts")),
            publish_root=Path(os.getenv("CAMPAIGN_PUBLISH_ROOT", "./data/publish")),
            model_root=Path(os.getenv("CAMPAIGN_MODEL_ROOT", "./data/models")),
            max_upload_bytes=_integer_environment("CAMPAIGN_MAX_UPLOAD_BYTES", 8 * 1024**3),
            worker_poll_seconds=_integer_environment("CAMPAIGN_WORKER_POLL_SECONDS", 5),
            transcription_provider=os.getenv("CAMPAIGN_TRANSCRIPTION_PROVIDER", "disabled"),
            whisper_model=os.getenv("CAMPAIGN_WHISPER_MODEL", "small.en"),
            whisper_device=os.getenv("CAMPAIGN_WHISPER_DEVICE", "cpu"),
            whisper_compute_type=os.getenv("CAMPAIGN_WHISPER_COMPUTE_TYPE", "int8"),
            whisper_cpu_threads=_integer_environment("CAMPAIGN_WHISPER_CPU_THREADS", 4),
            log_level=os.getenv("CAMPAIGN_LOG_LEVEL", "INFO").upper(),
        )

    def safe_summary(self) -> dict[str, object]:
        """Return diagnostic configuration without credentials."""
        return {
            "environment": self.environment,
            "host": self.host,
            "port": self.port,
            "database_configured": bool(self.database_url),
            "artifact_root": str(self.artifact_root),
            "publish_root": str(self.publish_root),
            "model_root": str(self.model_root),
            "max_upload_bytes": self.max_upload_bytes,
            "worker_poll_seconds": self.worker_poll_seconds,
            "transcription_provider": self.transcription_provider,
            "whisper_model": self.whisper_model,
            "whisper_device": self.whisper_device,
            "whisper_compute_type": self.whisper_compute_type,
            "whisper_cpu_threads": self.whisper_cpu_threads,
            "log_level": self.log_level,
        }


def _integer_environment(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc

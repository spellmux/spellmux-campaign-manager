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

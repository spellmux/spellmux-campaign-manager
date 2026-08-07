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
    diarization_provider: str = "disabled"
    diarization_model: str = "pyannote/speaker-diarization-community-1"
    diarization_device: str = "cpu"
    speaker_embedding_model: str = "pyannote/wespeaker-voxceleb-resnet34-LM"
    speaker_match_threshold: float = 0.70
    speaker_match_margin: float = 0.10
    huggingface_token: str | None = None
    analysis_provider: str = "disabled"
    analysis_model: str = "qwen3:4b"
    analysis_base_url: str = "http://ollama:11434"
    analysis_timeout_seconds: int = 21_600
    analysis_max_input_chars: int = 240_000
    # Measured on an 8 GB card holding a 4B model at q8_0, with the desktop using
    # 2 GB: 16k context left 100% of the model on the GPU, 32k also 100%, and 48k
    # spilled 15% to the CPU and halved output throughput. Chunks sized to fill 32k
    # cut a 28-chunk session to 8 and its extraction from 14 minutes to under 4,
    # with no truncation. The fixed rules and guide prefix costs about 7,000
    # characters per chunk, so a small chunk spends most of its budget on
    # boilerplate rather than transcript.
    analysis_context_tokens: int = 32_768
    analysis_max_output_tokens: int = 4_096
    analysis_chunk_chars: int = 32_000
    analysis_chunk_overlap_segments: int = 8
    # Ollama holds a model in VRAM for 5 minutes by default. On a single card
    # shared with transcription and image generation that blocks the next
    # stage, so the model is released sooner than the gap between chunks.
    analysis_keep_alive_seconds: int = 60
    otterwiki_repository_path: Path | None = None

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
            diarization_provider=os.getenv("CAMPAIGN_DIARIZATION_PROVIDER", "disabled"),
            diarization_model=os.getenv(
                "CAMPAIGN_DIARIZATION_MODEL",
                "pyannote/speaker-diarization-community-1",
            ),
            diarization_device=os.getenv("CAMPAIGN_DIARIZATION_DEVICE", "cpu"),
            speaker_embedding_model=os.getenv(
                "CAMPAIGN_SPEAKER_EMBEDDING_MODEL",
                "pyannote/wespeaker-voxceleb-resnet34-LM",
            ),
            speaker_match_threshold=_float_environment("CAMPAIGN_SPEAKER_MATCH_THRESHOLD", 0.70),
            speaker_match_margin=_float_environment("CAMPAIGN_SPEAKER_MATCH_MARGIN", 0.10),
            huggingface_token=os.getenv("CAMPAIGN_HUGGINGFACE_TOKEN") or None,
            analysis_provider=os.getenv("CAMPAIGN_ANALYSIS_PROVIDER", "disabled"),
            analysis_model=os.getenv("CAMPAIGN_ANALYSIS_MODEL", "qwen3:4b"),
            analysis_base_url=os.getenv("CAMPAIGN_ANALYSIS_BASE_URL", "http://ollama:11434").rstrip("/"),
            analysis_timeout_seconds=_integer_environment("CAMPAIGN_ANALYSIS_TIMEOUT_SECONDS", 21_600),
            analysis_max_input_chars=_integer_environment("CAMPAIGN_ANALYSIS_MAX_INPUT_CHARS", 240_000),
            analysis_context_tokens=_integer_environment("CAMPAIGN_ANALYSIS_CONTEXT_TOKENS", 32_768),
            analysis_max_output_tokens=_integer_environment(
                "CAMPAIGN_ANALYSIS_MAX_OUTPUT_TOKENS", 4_096
            ),
            analysis_chunk_chars=_integer_environment("CAMPAIGN_ANALYSIS_CHUNK_CHARS", 32_000),
            analysis_chunk_overlap_segments=_integer_environment(
                "CAMPAIGN_ANALYSIS_CHUNK_OVERLAP_SEGMENTS", 8
            ),
            analysis_keep_alive_seconds=_integer_environment(
                "CAMPAIGN_ANALYSIS_KEEP_ALIVE_SECONDS", 60
            ),
            otterwiki_repository_path=(
                Path(value) if (value := os.getenv("CAMPAIGN_OTTERWIKI_REPOSITORY_PATH")) else None
            ),
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
            "diarization_provider": self.diarization_provider,
            "diarization_model": self.diarization_model,
            "diarization_device": self.diarization_device,
            "speaker_embedding_model": self.speaker_embedding_model,
            "speaker_match_threshold": self.speaker_match_threshold,
            "speaker_match_margin": self.speaker_match_margin,
            "huggingface_token_configured": bool(self.huggingface_token),
            "analysis_provider": self.analysis_provider,
            "analysis_model": self.analysis_model,
            "analysis_base_url": self.analysis_base_url,
            "analysis_timeout_seconds": self.analysis_timeout_seconds,
            "analysis_max_input_chars": self.analysis_max_input_chars,
            "analysis_context_tokens": self.analysis_context_tokens,
            "analysis_max_output_tokens": self.analysis_max_output_tokens,
            "analysis_chunk_chars": self.analysis_chunk_chars,
            "analysis_chunk_overlap_segments": self.analysis_chunk_overlap_segments,
            "analysis_keep_alive_seconds": self.analysis_keep_alive_seconds,
            "otterwiki_publishing_configured": self.otterwiki_repository_path is not None,
            "log_level": self.log_level,
        }


def _float_environment(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _integer_environment(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc

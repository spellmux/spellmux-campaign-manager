from pathlib import Path

from campaign_manager.config import Settings


def test_settings_defaults(monkeypatch) -> None:
    for name in (
        "CAMPAIGN_ENV",
        "CAMPAIGN_HOST",
        "CAMPAIGN_PORT",
        "CAMPAIGN_DATABASE_URL",
        "CAMPAIGN_ARTIFACT_ROOT",
        "CAMPAIGN_PUBLISH_ROOT",
        "CAMPAIGN_MODEL_ROOT",
        "CAMPAIGN_TRANSCRIPTION_PROVIDER",
        "CAMPAIGN_WHISPER_MODEL",
        "CAMPAIGN_WHISPER_DEVICE",
        "CAMPAIGN_WHISPER_COMPUTE_TYPE",
        "CAMPAIGN_WHISPER_CPU_THREADS",
        "CAMPAIGN_DIARIZATION_PROVIDER",
        "CAMPAIGN_DIARIZATION_MODEL",
        "CAMPAIGN_DIARIZATION_DEVICE",
        "CAMPAIGN_HUGGINGFACE_TOKEN",
        "CAMPAIGN_LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_environment()

    assert settings.environment == "development"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8088
    assert settings.artifact_root == Path("./data/artifacts")
    assert settings.model_root == Path("./data/models")
    assert settings.transcription_provider == "disabled"
    assert settings.diarization_provider == "disabled"
    assert settings.huggingface_token is None
    assert settings.log_level == "INFO"


def test_safe_summary_hides_database_credentials() -> None:
    settings = Settings(
        environment="test",
        host="localhost",
        port=8088,
        database_url="postgresql://user:very-secret@database/db",
        artifact_root=Path("artifacts"),
        publish_root=Path("publish"),
        model_root=Path("models"),
        max_upload_bytes=1024,
        worker_poll_seconds=1,
        transcription_provider="disabled",
        whisper_model="small.en",
        whisper_device="cpu",
        whisper_compute_type="int8",
        whisper_cpu_threads=4,
        log_level="INFO",
    )

    summary = settings.safe_summary()

    assert summary["database_configured"] is True
    assert "very-secret" not in repr(summary)

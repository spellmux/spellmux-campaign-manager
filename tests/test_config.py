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
        "CAMPAIGN_LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_environment()

    assert settings.environment == "development"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8088
    assert settings.artifact_root == Path("./data/artifacts")
    assert settings.log_level == "INFO"


def test_safe_summary_hides_database_credentials() -> None:
    settings = Settings(
        environment="test",
        host="localhost",
        port=8088,
        database_url="postgresql://user:very-secret@database/db",
        artifact_root=Path("artifacts"),
        publish_root=Path("publish"),
        log_level="INFO",
    )

    summary = settings.safe_summary()

    assert summary["database_configured"] is True
    assert "very-secret" not in repr(summary)


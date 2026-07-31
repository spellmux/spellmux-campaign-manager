from fastapi.testclient import TestClient

from campaign_manager.api import create_app
from campaign_manager.config import Settings


def test_health_endpoint(tmp_path) -> None:
    settings = Settings(
        environment="test",
        host="127.0.0.1",
        port=8088,
        database_url="postgresql://unused",
        artifact_root=tmp_path / "artifacts",
        publish_root=tmp_path / "publish",
        log_level="INFO",
    )
    client = TestClient(create_app(settings))

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["environment"] == "test"


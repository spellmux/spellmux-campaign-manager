from fastapi.testclient import TestClient

from campaign_manager.api import create_app
from campaign_manager.auth import hash_password
from campaign_manager.config import Settings
from campaign_manager.database import configure_database, session_factory
from campaign_manager.models import Base, User


def configured_client(tmp_path) -> TestClient:
    engine = configure_database(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with session_factory()() as database:
        database.add(
            User(
                email="gm@example.test",
                display_name="Game Master",
                password_hash=hash_password("correct horse battery staple"),
                is_instance_admin=True,
            )
        )
        database.commit()
    settings = Settings(
        environment="test",
        host="127.0.0.1",
        port=8088,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        artifact_root=tmp_path / "artifacts",
        publish_root=tmp_path / "publish",
        model_root=tmp_path / "models",
        max_upload_bytes=10 * 1024 * 1024,
        worker_poll_seconds=1,
        transcription_provider="disabled",
        whisper_model="small.en",
        whisper_device="cpu",
        whisper_compute_type="int8",
        whisper_cpu_threads=4,
        log_level="INFO",
    )
    return TestClient(create_app(settings))


def login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "GM@Example.Test", "password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    return response.json()["access_token"]


def test_authentication_and_current_user(tmp_path) -> None:
    client = configured_client(tmp_path)
    token = login(client)

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "gm@example.test"
    assert response.json()["is_instance_admin"] is True


def test_invalid_login_does_not_issue_token(tmp_path) -> None:
    client = configured_client(tmp_path)

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "gm@example.test", "password": "incorrect password"},
    )

    assert response.status_code == 401
    assert "access_token" not in response.text


def test_campaign_owner_can_create_and_list_campaign(tmp_path) -> None:
    client = configured_client(tmp_path)
    token = login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = client.post(
        "/api/v1/campaigns",
        headers=headers,
        json={"name": "The Wild Beyond the Witchlight", "description": "A test campaign"},
    )
    listed = client.get("/api/v1/campaigns", headers=headers)

    assert created.status_code == 201
    assert created.json()["slug"] == "the-wild-beyond-the-witchlight"
    assert created.json()["role"] == "owner"
    assert listed.status_code == 200
    assert listed.json() == [created.json()]


def test_campaign_endpoints_require_authentication(tmp_path) -> None:
    client = configured_client(tmp_path)

    assert client.get("/api/v1/campaigns").status_code == 401
    assert client.post("/api/v1/campaigns", json={"name": "Nope"}).status_code == 401


def test_readiness_checks_database(tmp_path) -> None:
    client = configured_client(tmp_path)

    response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def create_campaign_and_session(client: TestClient, headers: dict[str, str]) -> tuple[str, str]:
    campaign = client.post(
        "/api/v1/campaigns",
        headers=headers,
        json={"name": "Planebreaker"},
    ).json()
    game_session = client.post(
        f"/api/v1/campaigns/{campaign['id']}/sessions",
        headers=headers,
        json={"title": "Arrival at the Crossroads", "session_date": "2026-07-31"},
    )
    assert game_session.status_code == 201
    return campaign["id"], game_session.json()["id"]


def test_session_audio_upload_is_private_and_queues_job(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)

    uploaded = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/audio",
        headers=headers,
        files={"audio": ("table-session.m4a", b"local audio bytes", "audio/mp4")},
    )
    jobs = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/jobs",
        headers=headers,
    )

    assert uploaded.status_code == 201
    assert uploaded.json()["visibility"] == "gm"
    assert uploaded.json()["job"]["kind"] == "transcription"
    assert uploaded.json()["job"]["status"] == "queued"
    assert jobs.json()[0]["id"] == uploaded.json()["job"]["id"]
    stored_files = list((tmp_path / "artifacts").rglob("*.m4a"))
    assert len(stored_files) == 1
    assert stored_files[0].read_bytes() == b"local audio bytes"


def test_campaign_guide_stores_canonical_vocabulary(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, _session_id = create_campaign_and_session(client, headers)

    created = client.post(
        f"/api/v1/campaigns/{campaign_id}/guide",
        headers=headers,
        json={
            "kind": "character",
            "canonical_name": "Tasha",
            "aliases": ["Iggwilv", "  Natasha  "],
            "notes": "Use the canonical spelling Tasha.",
            "visibility": "gm",
        },
    )
    listed = client.get(f"/api/v1/campaigns/{campaign_id}/guide", headers=headers)

    assert created.status_code == 201
    assert created.json()["aliases"] == ["Iggwilv", "Natasha"]
    assert listed.json()[0]["canonical_name"] == "Tasha"


def test_pasted_transcript_is_private_source_without_diarization_job(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)

    added = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/text",
        headers=headers,
        json={
            "kind": "transcript",
            "filename": "old-session.txt",
            "content": "GM: You arrive at the crossroads.\nTasha: I inspect the archway.",
        },
    )
    jobs = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/jobs",
        headers=headers,
    )

    assert added.status_code == 201
    assert added.json()["kind"] == "source_transcript"
    assert added.json()["visibility"] == "gm"
    assert added.json()["job"] is None
    assert jobs.json() == []
    stored = list((tmp_path / "artifacts").rglob("*.md"))
    assert stored[0].read_text(encoding="utf-8").startswith("GM: You arrive")

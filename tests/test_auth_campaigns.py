import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from campaign_manager.api import create_app
from campaign_manager.auth import hash_password
from campaign_manager.config import Settings
from campaign_manager.database import configure_database, session_factory
from campaign_manager.models import Artifact, Base, GameSession, Job, ProcessingControl, User


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

    duplicate = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/audio",
        headers=headers,
        files={"audio": ("other-session.mp3", b"different audio", "audio/mpeg")},
    )
    assert duplicate.status_code == 409
    assert "create another session" in duplicate.json()["detail"]


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

    artifacts = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/artifacts",
        headers=headers,
    )
    content = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/artifacts/{added.json()['id']}/content",
        headers=headers,
    )
    assert artifacts.status_code == 200
    assert artifacts.json()[0]["original_filename"] == "old-session.txt"
    assert content.text.startswith("GM: You arrive")


def test_transcript_corrections_create_new_version(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)
    relative_path = f"{campaign_id}/{session_id}/transcript/raw.json"
    transcript_path = tmp_path / "artifacts" / relative_path
    transcript_path.parent.mkdir(parents=True)
    raw_document = {
        "schema_version": 1,
        "provider": "test",
        "segments": [{"id": 0, "start": 1.0, "end": 2.0, "text": "Kaylin enters."}],
    }
    transcript_path.write_text(json.dumps(raw_document), encoding="utf-8")
    with session_factory()() as database:
        game_session = database.get(GameSession, uuid.UUID(session_id))
        user = database.scalar(select(User))
        artifact = Artifact(
            session_id=game_session.id,
            kind="raw_transcript",
            relative_path=relative_path,
            original_filename="raw.json",
            media_type="application/json",
            size_bytes=transcript_path.stat().st_size,
            sha256="0" * 64,
            visibility="gm",
            created_by_id=user.id,
        )
        database.add(artifact)
        database.commit()
        artifact_id = artifact.id

    revised = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/transcripts/{artifact_id}/revisions",
        headers=headers,
        json={"segments": [{"id": 0, "text": "Caelen enters."}]},
    )
    raw_after = json.loads(transcript_path.read_text(encoding="utf-8"))
    corrected = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/artifacts/{revised.json()['id']}/content",
        headers=headers,
    )

    assert revised.status_code == 201
    assert revised.json()["kind"] == "corrected_transcript"
    assert raw_after["segments"][0]["text"] == "Kaylin enters."
    assert corrected.json()["segments"][0]["text"] == "Caelen enters."


def test_gm_can_validate_speaker_clip_and_approve_reference(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)

    profile = client.post(
        f"/api/v1/campaigns/{campaign_id}/speakers",
        headers=headers,
        json={"display_name": "Rob", "notes": "Game Master"},
    )
    reviewed = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/speaker-reviews",
        headers=headers,
        json={
            "cluster_label": "SPEAKER_00",
            "start_seconds": 42,
            "end_seconds": 50,
            "speaker_profile_id": profile.json()["id"],
            "disposition": "confirmed",
            "approved_reference": True,
            "notes": "Clean solo speech",
        },
    )
    listed = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/speaker-reviews",
        headers=headers,
    )

    assert profile.status_code == 201
    assert reviewed.status_code == 201
    assert reviewed.json()["speaker_name"] == "Rob"
    assert reviewed.json()["approved_reference"] is True
    assert listed.json() == [reviewed.json()]

    invalid = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/speaker-reviews",
        headers=headers,
        json={
            "cluster_label": "SPEAKER_01",
            "start_seconds": 60,
            "end_seconds": 70,
            "disposition": "uncertain",
            "approved_reference": True,
        },
    )
    assert invalid.status_code == 422
    assert "confirmed speaker" in invalid.json()["detail"]


def test_gm_can_classify_music_and_reopen_cluster(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)

    reviewed = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/speaker-reviews",
        headers=headers,
        json={
            "cluster_label": "SPEAKER_04",
            "start_seconds": 120,
            "end_seconds": 135,
            "disposition": "featured_song",
            "notes": "Player-created recap song",
        },
    )
    reopened = client.delete(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/speaker-reviews/SPEAKER_04",
        headers=headers,
    )
    listed = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/speaker-reviews",
        headers=headers,
    )

    assert reviewed.status_code == 201
    assert reviewed.json()["disposition"] == "featured_song"
    assert reviewed.json()["speaker_profile_id"] is None
    assert reopened.status_code == 204
    assert listed.json() == []


def test_admin_can_prioritize_cancel_and_pause_processing(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)
    with session_factory()() as database:
        job = Job(session_id=uuid.UUID(session_id), kind="analysis", status="queued", payload={})
        database.add(job)
        database.add(ProcessingControl(kind="analysis", paused=False))
        database.commit()
        job_id = str(job.id)

    prioritized = client.put(
        f"/api/v1/jobs/{job_id}/priority", headers=headers, json={"priority": 100}
    )
    paused = client.put(
        "/api/v1/processing-controls/analysis", headers=headers, json={"paused": True}
    )
    queue = client.get("/api/v1/jobs", headers=headers)
    cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)

    assert prioritized.status_code == 200
    assert prioritized.json()["priority"] == 100
    assert paused.status_code == 200
    assert paused.json()["paused"] is True
    assert queue.status_code == 200
    assert queue.json()[0]["campaign_id"] == campaign_id
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancel_requested"] is True


def test_gm_can_queue_diarization_after_normalized_audio_exists(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)
    relative_path = f"{campaign_id}/{session_id}/normalized/source.wav"
    audio_path = tmp_path / "artifacts" / relative_path
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"RIFF test")
    with session_factory()() as database:
        user = database.scalar(select(User))
        database.add(
            Artifact(
                session_id=uuid.UUID(session_id),
                kind="normalized_audio",
                relative_path=relative_path,
                original_filename="source.wav",
                media_type="audio/wav",
                size_bytes=audio_path.stat().st_size,
                sha256="1" * 64,
                visibility="gm",
                created_by_id=user.id,
            )
        )
        database.commit()

    queued = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/diarization",
        headers=headers,
    )
    duplicate = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/diarization",
        headers=headers,
    )

    assert queued.status_code == 202
    assert queued.json()["kind"] == "diarization"
    assert queued.json()["status"] == "queued"
    assert duplicate.status_code == 409

import uuid
from pathlib import Path

from sqlalchemy import select
from test_auth_campaigns import configured_client, create_campaign_and_session, login

from campaign_manager.config import Settings
from campaign_manager.database import session_factory
from campaign_manager.enrollment import (
    cosine_similarity,
    enroll_campaign_speakers,
    match_cluster_to_speakers,
    process_enrollment_job,
)
from campaign_manager.models import (
    Artifact,
    GameSession,
    Job,
    SpeakerProfile,
    SpeakerReview,
    SpeakerVoiceprint,
)


def _settings(tmp_path) -> Settings:
    return Settings(
        environment="test", host="127.0.0.1", port=8088,
        database_url=f"sqlite:///{tmp_path / 'test.db'}", artifact_root=tmp_path / "artifacts",
        publish_root=tmp_path / "publish", model_root=tmp_path / "models",
        max_upload_bytes=10_000_000, worker_poll_seconds=1, transcription_provider="disabled",
        whisper_model="small.en", whisper_device="cpu", whisper_compute_type="int8",
        whisper_cpu_threads=1, log_level="INFO",
    )


def _audio(settings: Settings, campaign_id, session_id) -> str:
    relative = Path(str(campaign_id)) / str(session_id) / "normalized.wav"
    path = settings.artifact_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF")
    return str(relative)


def _enrolled_campaign(tmp_path, *, approve_second: bool = True):
    """Two speakers with reference clips, one of them across two sessions."""
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)
    settings = _settings(tmp_path)
    with session_factory()() as database:
        game_session = database.get(GameSession, uuid.UUID(session_id))
        creator_id = game_session.created_by_id
        database.add(Artifact(
            session_id=game_session.id, kind="normalized_audio",
            relative_path=_audio(settings, campaign_id, session_id),
            original_filename="a.wav", media_type="audio/wav", size_bytes=4,
            sha256="x" * 64, visibility="gm", created_by_id=creator_id,
        ))
        first = SpeakerProfile(
            campaign_id=uuid.UUID(campaign_id), display_name="Ana", created_by_id=creator_id)
        second = SpeakerProfile(
            campaign_id=uuid.UUID(campaign_id), display_name="Bo", created_by_id=creator_id)
        database.add_all([first, second])
        database.flush()
        database.add_all([
            # Two approved clips for Ana; the longer one must dominate the mean.
            SpeakerReview(
                session_id=game_session.id, cluster_label="SPEAKER_00", start_seconds=0,
                end_seconds=2, speaker_profile_id=first.id, disposition="confirmed",
                approved_reference=True, reviewed_by_id=creator_id),
            SpeakerReview(
                session_id=game_session.id, cluster_label="SPEAKER_00", start_seconds=10,
                end_seconds=18, speaker_profile_id=first.id, disposition="confirmed",
                approved_reference=True, reviewed_by_id=creator_id),
            # Bo is confirmed but the clip is not approved as a reference.
            SpeakerReview(
                session_id=game_session.id, cluster_label="SPEAKER_01", start_seconds=20,
                end_seconds=25, speaker_profile_id=second.id, disposition="confirmed",
                approved_reference=approve_second, reviewed_by_id=creator_id),
            # Music carries no person and must never be enrolled.
            SpeakerReview(
                session_id=game_session.id, cluster_label="SPEAKER_02", start_seconds=30,
                end_seconds=40, speaker_profile_id=None, disposition="music",
                approved_reference=False, reviewed_by_id=creator_id),
        ])
        database.commit()
    return client, headers, campaign_id, settings


def test_enrollment_uses_only_approved_reference_clips(tmp_path) -> None:
    _client, _headers, campaign_id, settings = _enrolled_campaign(tmp_path, approve_second=False)
    calls = []

    def fake_embed(path: Path, start: float, end: float) -> list[float]:
        calls.append((start, end))
        # Distinct vectors so the duration weighting is observable.
        return [1.0, 0.0] if end - start <= 2 else [0.0, 1.0]

    with session_factory()() as database:
        summary = enroll_campaign_speakers(
            database, settings, uuid.UUID(campaign_id), fake_embed, "test-model")

        # Only Ana's two approved clips were embedded; Bo's unapproved clip and
        # the music cluster were never touched.
        assert sorted(calls) == [(0.0, 2.0), (10.0, 18.0)]
        assert summary.enrolled == 1
        assert summary.failures == ()

        prints = database.scalars(select(SpeakerVoiceprint)).all()
        assert len(prints) == 1
        voiceprint = prints[0]
        assert voiceprint.sample_count == 2
        assert voiceprint.sample_seconds == 10.0
        # 2s of [1,0] and 8s of [0,1] weights the centroid toward the longer clip.
        assert voiceprint.embedding[0] < voiceprint.embedding[1]


def test_enrollment_is_idempotent_and_reflects_changed_reviews(tmp_path) -> None:
    _client, _headers, campaign_id, settings = _enrolled_campaign(tmp_path)

    def embed_ones(path: Path, start: float, end: float) -> list[float]:
        return [1.0, 1.0]

    with session_factory()() as database:
        first = enroll_campaign_speakers(
            database, settings, uuid.UUID(campaign_id), embed_ones, "test-model")
        assert first.enrolled == 2

        # Re-running must refresh in place rather than accumulate rows.
        second = enroll_campaign_speakers(
            database, settings, uuid.UUID(campaign_id), embed_ones, "test-model")
        assert second.enrolled == 2
        assert len(database.scalars(select(SpeakerVoiceprint)).all()) == 2

        # A different embedding model is kept separately, since the vectors are
        # not comparable across models.
        third = enroll_campaign_speakers(
            database, settings, uuid.UUID(campaign_id), embed_ones, "other-model")
        assert third.enrolled == 2
        assert len(database.scalars(select(SpeakerVoiceprint)).all()) == 4


def test_matching_requires_both_closeness_and_separation() -> None:
    ana = SpeakerVoiceprint(
        speaker_profile_id=uuid.uuid4(), embedding_model="m", embedding=[1.0, 0.0])
    bo = SpeakerVoiceprint(
        speaker_profile_id=uuid.uuid4(), embedding_model="m", embedding=[0.0, 1.0])

    # Clearly Ana: high similarity and a wide margin over Bo.
    match, score, separation = match_cluster_to_speakers(
        [0.99, 0.05], [ana, bo], threshold=0.7, margin=0.1)
    assert match is ana
    assert score > 0.9 and separation > 0.1

    # An unenrolled guest sits between the regulars: close to nobody, and the
    # margin test refuses rather than guessing the nearest regular.
    match, score, separation = match_cluster_to_speakers(
        [0.7, 0.7], [ana, bo], threshold=0.7, margin=0.1)
    assert match is None
    assert separation < 0.1

    assert match_cluster_to_speakers([1.0, 0.0], [], threshold=0.7, margin=0.1)[0] is None


def test_cosine_similarity_handles_degenerate_vectors() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert cosine_similarity([1.0], [1.0, 0.0]) == 0.0
    assert cosine_similarity([], []) == 0.0


def test_enrollment_endpoint_queues_a_job_and_lists_voiceprints(tmp_path) -> None:
    client, headers, campaign_id, settings = _enrolled_campaign(tmp_path)

    queued = client.post(f"/api/v1/campaigns/{campaign_id}/voiceprints", headers=headers)
    assert queued.status_code == 202
    assert client.post(
        f"/api/v1/campaigns/{campaign_id}/voiceprints", headers=headers
    ).status_code == 409

    def embed_ones(path: Path, start: float, end: float) -> list[float]:
        return [0.6, 0.8]

    with session_factory()() as database:
        job = database.get(Job, uuid.UUID(queued.json()["id"]))
        process_enrollment_job(database, settings, job, embed_ones, "test-model")
        assert job.payload["enrollment"]["enrolled"] == 2
        assert job.payload["enrollment"]["embedding_model"] == "test-model"

    listed = client.get(f"/api/v1/campaigns/{campaign_id}/voiceprints", headers=headers).json()
    assert [item["speaker_name"] for item in listed] == ["Ana", "Bo"]
    assert all(item["dimensions"] == 2 for item in listed)
    assert all(item["sample_count"] >= 1 for item in listed)


def test_enrollment_endpoint_refuses_without_approved_references(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, _session_id = create_campaign_and_session(client, headers)

    response = client.post(f"/api/v1/campaigns/{campaign_id}/voiceprints", headers=headers)

    assert response.status_code == 409
    assert "voice reference" in response.json()["detail"]

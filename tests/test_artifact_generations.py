"""Re-running transcription or diarization replaces the result without losing it."""

import uuid

from sqlalchemy import select
from test_auth_campaigns import configured_client, create_campaign_and_session, login

from test_analysis import _settings

from campaign_manager.database import session_factory
from campaign_manager.diarization import DiarizationResult, process_diarization_job
from campaign_manager.jobs import complete_job
from campaign_manager.models import Artifact, Job, SpeakerReview
from campaign_manager.transcription import process_transcription_job


def transcriber(text: str):
    def transcribe(path, prompt, hotwords):
        return {
            "language": "en", "duration": 20.0,
            "segments": [{"id": 0, "start": 1.0, "end": 4.0, "text": text}],
        }

    return transcribe


def diarizer(labels: tuple[str, str]):
    def diarize(path):
        return DiarizationResult(
            turns=[(1.0, 9.0, labels[0]), (12.0, 20.0, labels[1])], embeddings={}
        )

    return diarize


def uploaded_session(client, headers):
    campaign_id, session_id = create_campaign_and_session(client, headers)
    uploaded = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/audio",
        headers=headers,
        files={"audio": ("table.wav", _silent_wav(), "audio/wav")},
    ).json()
    return campaign_id, session_id, uploaded


def _silent_wav() -> bytes:
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"\x00\x00" * 16_000 * 20)
    return buffer.getvalue()


def run_job(job_id, settings, worker, adapter):
    with session_factory()() as database:
        job = database.get(Job, uuid.UUID(str(job_id)))
        worker(database, settings, job, adapter)
        complete_job(database, job)


def test_re_transcription_supersedes_the_previous_transcript(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    settings = _settings(tmp_path)
    campaign_id, session_id, uploaded = uploaded_session(client, headers)
    run_job(
        uploaded["job"]["id"], settings, process_transcription_job,
        transcriber("Caelen opens the door."),
    )

    requeued = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/transcription", headers=headers
    )
    assert requeued.status_code == 202
    # The audio is reused rather than re-uploaded, and the job says what it replaces.
    with session_factory()() as database:
        job = database.get(Job, uuid.UUID(requeued.json()["id"]))
        assert str(job.artifact_id) == uploaded["id"]
        assert "replaces_artifact_id" in job.payload
    run_job(
        requeued.json()["id"], settings, process_transcription_job,
        transcriber("Caelen picks the lock."),
    )

    artifacts = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/artifacts", headers=headers
    ).json()
    transcripts = [a for a in artifacts if a["kind"] == "raw_transcript"]
    # Both generations are kept; exactly one is live.
    assert len(transcripts) == 2
    assert len([a for a in transcripts if a["superseded_at"] is None]) == 1
    live = next(a for a in transcripts if a["superseded_at"] is None)

    # Analysis picks the live transcript, not the retired one.
    queued = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis", headers=headers, json={}
    ).json()
    with session_factory()() as database:
        assert str(database.get(Job, uuid.UUID(queued["id"])).artifact_id) == live["id"]


def test_re_transcription_is_refused_while_a_heavy_job_is_pending(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id, uploaded = uploaded_session(client, headers)

    # The upload's own transcription job is still queued.
    refused = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/transcription", headers=headers
    )
    assert refused.status_code == 409
    assert "transcription job" in refused.json()["detail"]


def test_re_diarization_replaces_clusters_and_retires_their_reviews(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    settings = _settings(tmp_path)
    campaign_id, session_id, uploaded = uploaded_session(client, headers)
    run_job(
        uploaded["job"]["id"], settings, process_transcription_job,
        transcriber("Caelen opens the door."),
    )

    first = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/diarization", headers=headers
    ).json()
    run_job(first["id"], settings, process_diarization_job, diarizer(("SPEAKER_00", "SPEAKER_01")))

    speaker = client.post(
        f"/api/v1/campaigns/{campaign_id}/speakers", headers=headers,
        json={"display_name": "Rob", "notes": ""},
    ).json()
    reviewed = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/speaker-reviews", headers=headers,
        json={
            "cluster_label": "SPEAKER_00", "start_seconds": 1, "end_seconds": 9,
            "speaker_profile_id": speaker["id"], "disposition": "confirmed",
            "approved_reference": True, "notes": "",
        },
    )
    assert reviewed.status_code == 201

    # Diarizing again is allowed now, and supersedes the first generation.
    second = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/diarization", headers=headers
    )
    assert second.status_code == 202
    with session_factory()() as database:
        job = database.get(Job, uuid.UUID(second.json()["id"]))
        assert "replaces_artifact_id" in job.payload
    run_job(
        second.json()["id"], settings, process_diarization_job,
        diarizer(("SPEAKER_00", "SPEAKER_01")),
    )

    artifacts = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/artifacts", headers=headers
    ).json()
    diarizations = [a for a in artifacts if a["kind"] == "diarization"]
    assert len(diarizations) == 2
    assert len([a for a in diarizations if a["superseded_at"] is None]) == 1

    # The review described the old generation's SPEAKER_00, who is not necessarily
    # this generation's SPEAKER_00, so it is not offered as current work.
    assert client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/speaker-reviews", headers=headers
    ).json() == []
    # It is kept, not deleted: restoring the earlier generation restores the work.
    with session_factory()() as database:
        stored = database.scalars(select(SpeakerReview)).all()
        assert len(stored) == 1
        retired = database.scalar(select(Artifact).where(
            Artifact.kind == "diarization", Artifact.superseded_at.is_not(None)
        ))
        assert stored[0].diarization_artifact_id == retired.id


def test_reviews_of_the_live_diarization_still_reach_analysis(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    settings = _settings(tmp_path)
    campaign_id, session_id, uploaded = uploaded_session(client, headers)
    run_job(
        uploaded["job"]["id"], settings, process_transcription_job,
        transcriber("Caelen opens the door."),
    )
    diarization = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/diarization", headers=headers
    ).json()
    run_job(
        diarization["id"], settings, process_diarization_job,
        diarizer(("SPEAKER_00", "SPEAKER_01")),
    )
    speaker = client.post(
        f"/api/v1/campaigns/{campaign_id}/speakers", headers=headers,
        json={"display_name": "Rob", "notes": ""},
    ).json()
    client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/speaker-reviews", headers=headers,
        json={
            "cluster_label": "SPEAKER_00", "start_seconds": 1, "end_seconds": 9,
            "speaker_profile_id": speaker["id"], "disposition": "confirmed",
            "approved_reference": False, "notes": "",
        },
    )

    listed = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/speaker-reviews", headers=headers
    ).json()
    assert [item["cluster_label"] for item in listed] == ["SPEAKER_00"]
    with session_factory()() as database:
        review = database.scalar(select(SpeakerReview))
        live = database.scalar(select(Artifact).where(
            Artifact.kind == "diarization", Artifact.superseded_at.is_(None)
        ))
        assert review.diarization_artifact_id == live.id

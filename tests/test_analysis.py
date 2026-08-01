import uuid
from dataclasses import replace

from sqlalchemy import select
from test_auth_campaigns import configured_client, create_campaign_and_session, login

from campaign_manager.analysis import (
    AnalysisResult,
    build_analysis_prompt,
    ollama_status,
    process_analysis_job,
)
from campaign_manager.database import session_factory
from campaign_manager.models import AnalysisProposal, CampaignGuideEntry, GameSession, Job


def test_analysis_queue_selects_best_source_and_prevents_duplicate(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)
    notes = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/text",
        headers=headers,
        json={"kind": "notes", "filename": "notes.md", "content": "The party entered Wonderland."},
    ).json()
    transcript = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/text",
        headers=headers,
        json={"kind": "transcript", "filename": "session.vtt", "content": "WEBVTT\n\n00:00:12.000 --> 00:00:16.000\nCaelen opens the door."},
    ).json()

    queued = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis",
        headers=headers,
        json={},
    )
    assert queued.status_code == 202
    with session_factory()() as database:
        job = database.get(Job, uuid.UUID(queued.json()["id"]))
        assert str(job.artifact_id) == transcript["id"]
    assert client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis",
        headers=headers,
        json={"source_artifact_id": notes["id"]},
    ).status_code == 409


def test_analysis_job_creates_grounded_review_proposals(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)
    source = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/text",
        headers=headers,
        json={
            "kind": "transcript", "filename": "session.vtt",
            "content": "WEBVTT\n\n00:00:12.000 --> 00:00:16.000\nCaelen opens the door.\n\n00:00:20.000 --> 00:00:24.000\nThe party enters Wonderland.",
        },
    ).json()
    client.post(
        f"/api/v1/campaigns/{campaign_id}/guide", headers=headers,
        json={"kind": "character", "canonical_name": "Caelen", "aliases": ["Kalen"], "notes": "Player character", "visibility": "gm"},
    )
    queued = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis",
        headers=headers, json={"source_artifact_id": source["id"]},
    ).json()
    captured = {}

    def fake_analyze(prompt, model, schema):
        captured.update(prompt=prompt, model=model, schema=schema)
        return AnalysisResult.model_validate({"proposals": [{
            "kind": "character", "title": "Caelen", "body": "Opened the door.",
            "aliases": [], "confidence": 0.9, "visibility": "gm",
            "evidence": [{"segment_ids": [0], "quote": "Caelen opens the door."}],
        }]}), {"eval_count": 42}

    with session_factory()() as database:
        job = database.get(Job, uuid.UUID(queued["id"]))
        process_analysis_job(database, _settings(tmp_path), job, fake_analyze)
        proposal = database.scalar(select(AnalysisProposal))
        assert proposal.title == "Caelen"
        assert proposal.evidence[0]["artifact_id"] == source["id"]
        assert proposal.evidence[0]["start_seconds"] == 12.0
        assert proposal.evidence[0]["end_seconds"] == 16.0
        assert proposal.run_metadata["eval_count"] == 42
    assert "character: Caelen" in captured["prompt"]
    assert "[0 12.00-16.00s]" in captured["prompt"]


def _settings(tmp_path):
    from campaign_manager.config import Settings

    return Settings(
        environment="test", host="127.0.0.1", port=8088,
        database_url=f"sqlite:///{tmp_path / 'test.db'}", artifact_root=tmp_path / "artifacts",
        publish_root=tmp_path / "publish", model_root=tmp_path / "models",
        max_upload_bytes=10_000_000, worker_poll_seconds=1, transcription_provider="disabled",
        whisper_model="small.en", whisper_device="cpu", whisper_compute_type="int8",
        whisper_cpu_threads=4, log_level="INFO", analysis_provider="ollama",
    )


def test_prompt_respects_input_limit(tmp_path) -> None:
    session = GameSession(title="Test", description="", campaign_id=uuid.uuid4(), created_by_id=uuid.uuid4())
    guide = [CampaignGuideEntry(
        campaign_id=session.campaign_id, kind="location", canonical_name="Wonderland",
        aliases=[], notes="", visibility="gm", created_by_id=session.created_by_id,
    )]
    prompt, included = build_analysis_prompt(
        session, guide, [{"start": index, "end": index + 1, "text": "x" * 40} for index in range(50)], 1_000,
    )
    assert len(prompt) <= 1_000
    assert 0 < len(included) < 50


def test_prompt_includes_resolved_speaker_attribution(tmp_path) -> None:
    session = GameSession(title="Test", description="", campaign_id=uuid.uuid4(), created_by_id=uuid.uuid4())
    prompt, _ = build_analysis_prompt(
        session, [], [{"start": 12.0, "end": 15.0, "speaker_name": "Rob", "text": "I open the door."}], 2_000,
    )

    assert "Rob: I open the door." in prompt
    assert 'Return exactly one JSON object with a "proposals" array' in prompt
    assert "session_summary, character, location" in prompt


def test_analysis_status_is_disabled_by_default(tmp_path) -> None:
    status = ollama_status(replace(_settings(tmp_path), analysis_provider="disabled"))
    assert status == {"configured": False, "ready": False, "model": "qwen3:4b", "models": []}

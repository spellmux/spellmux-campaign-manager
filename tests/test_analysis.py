import uuid
from dataclasses import replace

import pytest
from sqlalchemy import select
from test_auth_campaigns import configured_client, create_campaign_and_session, login

from campaign_manager.analysis import (
    AnalysisResult,
    ExtractedProposal,
    build_analysis_prompt,
    build_analysis_prompts,
    merge_chunk_proposals,
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


def test_analysis_job_rejects_empty_model_result(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)
    source = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/text",
        headers=headers,
        json={"kind": "notes", "filename": "notes.md", "content": "The party entered Wonderland."},
    ).json()
    queued = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis",
        headers=headers,
        json={"source_artifact_id": source["id"]},
    ).json()

    def empty_analyze(prompt, model, schema):
        return AnalysisResult(proposals=[]), {"done_reason": "stop"}

    with session_factory()() as database:
        job = database.get(Job, uuid.UUID(queued["id"]))
        with pytest.raises(ValueError, match="returned no findings"):
            process_analysis_job(database, _settings(tmp_path), job, empty_analyze)
        assert database.scalar(select(AnalysisProposal)) is None


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
    max_chars = 2_000
    session = GameSession(title="Test", description="", campaign_id=uuid.uuid4(), created_by_id=uuid.uuid4())
    guide = [CampaignGuideEntry(
        campaign_id=session.campaign_id, kind="location", canonical_name="Wonderland",
        aliases=[], notes="", visibility="gm", created_by_id=session.created_by_id,
    )]
    prompt, included = build_analysis_prompt(
        session, guide, [{"start": index, "end": index + 1, "text": "x" * 40} for index in range(50)], max_chars,
    )
    assert len(prompt) <= max_chars
    assert 0 < len(included) < 50


def test_analysis_prompts_cover_long_source_with_overlap_and_global_ids(tmp_path) -> None:
    session = GameSession(
        title="Test", description="", campaign_id=uuid.uuid4(), created_by_id=uuid.uuid4()
    )
    segments = [
        {"start": index, "end": index + 1, "text": f"segment-{index} " + "x" * 120}
        for index in range(40)
    ]

    prompts = build_analysis_prompts(session, [], segments, max_chars=2_200, overlap_segments=2)

    assert len(prompts) > 1
    covered = {index for _prompt, included in prompts for index, _segment in included}
    assert covered == set(range(40))
    second_prompt, second_included = prompts[1]
    assert f"[{second_included[0][0]} " in second_prompt
    assert second_included[0][0] <= prompts[0][1][-1][0]
    assert {index for index, _segment in prompts[0][1]} & {
        index for index, _segment in second_included
    }


def test_chunk_merge_deduplicates_entities_and_preserves_evidence() -> None:
    first = ExtractedProposal.model_validate({
        "kind": "character", "title": "Caelen", "body": "Opened the door.",
        "aliases": [], "confidence": 0.8, "visibility": "player",
        "evidence": [{"segment_ids": [4], "quote": "I open the door."}],
    })
    second = ExtractedProposal.model_validate({
        "kind": "character", "title": "caelen", "body": "Found the key.",
        "aliases": ["Kalen"], "confidence": 0.9, "visibility": "gm",
        "evidence": [{"segment_ids": [8], "quote": "The key is here."}],
    })

    merged = merge_chunk_proposals([
        ([first], [(4, {"start": 12.0, "end": 15.0})]),
        ([second], [(8, {"start": 30.0, "end": 33.0})]),
    ])

    assert len(merged) == 1
    proposal, evidence = merged[0]
    assert proposal.body == "Opened the door.\n\nFound the key."
    assert proposal.aliases == ["Kalen"]
    assert proposal.confidence == 0.9
    assert proposal.visibility == "gm"
    assert [item["start_seconds"] for item in evidence] == [12.0, 30.0]


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

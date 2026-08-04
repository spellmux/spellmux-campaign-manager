import json
import uuid
from dataclasses import replace

import pytest
from sqlalchemy import select
from test_auth_campaigns import configured_client, create_campaign_and_session, login

from campaign_manager.analysis import (
    ANALYSIS_RESPONSE_SCHEMA,
    PROPOSAL_KINDS,
    AnalysisResult,
    ExtractedProposal,
    build_analysis_prompt,
    build_analysis_prompts,
    canonicalize_character_kinds,
    consolidate_analysis,
    merge_chunk_proposals,
    ollama_analyzer,
    ollama_status,
    parse_analysis_content,
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
        assert job.payload["analysis_progress"]["stage"] == "complete"
        assert job.payload["analysis_progress"]["finding_count"] == 1
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


def test_analysis_fails_only_when_every_chunk_fails(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)
    cues = "\n\n".join(
        f"00:00:{index:02d}.000 --> 00:00:{index:02d}.900\nCaelen explores room {index}. "
        + "Details " * 20
        for index in range(30)
    )
    source = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/text",
        headers=headers,
        json={"kind": "transcript", "filename": "long.vtt", "content": f"WEBVTT\n\n{cues}"},
    ).json()
    queued = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis",
        headers=headers,
        json={"source_artifact_id": source["id"]},
    ).json()
    calls = 0

    def fail_every_chunk(prompt, model, schema):
        nonlocal calls
        calls += 1
        raise ValueError("truncated model response")

    with session_factory()() as database:
        job = database.get(Job, uuid.UUID(queued["id"]))
        settings = replace(_settings(tmp_path), analysis_chunk_chars=2_200)
        # Individual chunk failures are survivable, but a run that salvaged
        # nothing must still fail loudly instead of marking the source complete.
        with pytest.raises(ValueError, match="returned no findings"):
            process_analysis_job(database, settings, job, fail_every_chunk)
        assert calls > 1
        assert database.scalar(select(AnalysisProposal)) is None
        assert job.payload["analysis_progress"]["failed_chunks"] == calls
        assert "truncated model response" in job.payload["chunk_failures"][0]["error"]


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


def test_analysis_normalizes_overbroad_evidence_and_derives_missing_quote() -> None:
    proposal = ExtractedProposal.model_validate({
        "kind": "session_summary", "title": "Session recap", "body": "The party explored.",
        "aliases": [], "confidence": 0.8, "visibility": "player",
        "evidence": [{"segment_ids": list(range(224))}],
    })

    assert proposal.evidence[0].segment_ids == [0, 1, 2, 3, 4, 219, 220, 221, 222, 223]
    merged = merge_chunk_proposals([(
        [proposal],
        [(index, {"start": float(index), "end": float(index + 1), "text": f"Line {index}."})
         for index in range(224)],
    )])

    _, evidence = merged[0]
    assert evidence[0]["quote"] == "Line 0. Line 1. Line 2."
    assert evidence[0]["start_seconds"] == 0.0
    assert evidence[0]["end_seconds"] == 224.0


def test_analysis_accepts_singular_string_ids_and_caps_evidence_entries() -> None:
    proposal = ExtractedProposal.model_validate({
        "kind": "session_summary", "title": "Session recap", "body": "The party talked.",
        "aliases": [], "confidence": 0.8, "visibility": "player",
        "evidence": [
            {"segment_id": str(index), "quote": f"Line {index}."} for index in range(78)
        ],
    })

    assert len(proposal.evidence) == 20
    assert [item.segment_ids for item in proposal.evidence[:2]] == [[0], [1]]
    assert [item.segment_ids for item in proposal.evidence[-2:]] == [[76], [77]]


def test_analysis_normalizes_structured_aliases_from_model_output() -> None:
    proposal = ExtractedProposal.model_validate({
        "kind": "player_character", "title": "Caelen", "body": "A local hero.",
        "aliases": [
            {"name": "Cailin", "description": "phonetic spelling"},
            {"alias": "Kalen"},
            " Caelen Meir Harpell ",
            {"description": "missing a usable alias"},
        ],
        "confidence": 0.9, "visibility": "gm", "evidence": [],
    })

    assert proposal.aliases == ["Cailin", "Kalen", "Caelen Meir Harpell"]


def test_analysis_normalizes_compact_evidence_and_misplaced_visibility() -> None:
    proposal = ExtractedProposal.model_validate({
        "kind": "npc", "title": "Mayor Nez", "body": "Mayor of Dinah.",
        "lane": "gm", "aliases": [], "confidence": 0.9,
        "evidence": ["[84 502.0-510.0s] Tim: The mayor approaches."],
    })

    assert proposal.lane == "story"
    assert proposal.visibility == "gm"
    assert proposal.evidence[0].segment_ids == [84]
    assert proposal.evidence[0].quote == "Tim: The mayor approaches."

    follow_up = ExtractedProposal.model_validate({
        "kind": "meta", "title": "Check heroic inspiration later", "body": "Rules research.",
        "aliases": [], "confidence": 0.8, "visibility": "gm", "evidence": [],
    })
    assert follow_up.kind == "follow_up"
    assert follow_up.lane == "meta"

    compact_ids = ExtractedProposal.model_validate({
        "kind": "scene", "title": "Rabbit chase", "body": "The party gives chase.",
        "aliases": [], "confidence": 0.9, "visibility": "player",
        "evidence": [934, "935", 936],
    })
    assert compact_ids.evidence[0].segment_ids == [934, 935, 936]


def test_truncated_model_output_salvages_complete_proposals() -> None:
    # Reproduces a real Sharn response: eval_count hit num_predict and the JSON
    # stopped mid-string, which previously failed the whole 36-chunk job.
    content = (
        '{\n "proposals": [\n'
        '  {"kind": "location", "title": "Tea Party Estate", "body": "Behind a picket fence.",'
        '   "aliases": [], "confidence": 0.9, "visibility": "player",'
        '   "evidence": [{"segment_ids": [12], "quote": "the estate"}]},\n'
        '  {"kind": "scene", "title": "Arrival", "body": "The party arrives.",'
        '   "aliases": [], "confidence": 0.8, "visibility": "player",'
        '   "evidence": [{"segment_ids": [14], "quote": "we go in"}]},\n'
        '  {"kind": "npc", "title": "Hatter", "body": "A host who never finish'
    )

    result, diagnostics = parse_analysis_content(content)

    assert [proposal.title for proposal in result.proposals] == ["Tea Party Estate", "Arrival"]
    assert diagnostics["recovered_from_truncation"] is True


def test_recap_keyed_by_kind_instead_of_proposals_is_recovered() -> None:
    # The real narrative-section response from Sharn: a finished recap returned as
    # paragraphs under the section's kind, which was previously discarded whole.
    content = json.dumps({"session_summary": [
        "The session opened with the party arriving at The Cozy Landing Zone.",
        "Investigative efforts turned toward the mysterious White Rabbit.",
    ]})

    result, diagnostics = parse_analysis_content(content)

    assert diagnostics["recovered_from_kind_keys"] is True
    assert len(result.proposals) == 1
    recap = result.proposals[0]
    assert recap.kind == "session_summary"
    assert recap.lane == "story"
    # Paragraphs become one recap, not one finding each.
    assert recap.body.count("\n\n") == 1
    assert "White Rabbit" in recap.body


def test_kind_keyed_lists_and_plurals_become_individual_proposals() -> None:
    content = json.dumps({
        "scenes": ["The party gives chase. It ends badly."],
        "npcs": [{"title": "Mayor Nez", "body": "The mayor.", "confidence": 0.7}],
        "unrelated_key": ["ignored"],
    })

    result, _diagnostics = parse_analysis_content(content)

    by_kind = {proposal.kind: proposal for proposal in result.proposals}
    assert set(by_kind) == {"scene", "npc"}
    # A bare string carries no title, so the leading sentence becomes one.
    assert by_kind["scene"].title == "The party gives chase."
    assert by_kind["npc"].title == "Mayor Nez"


def test_response_schema_is_flat_enough_for_grammar_compilation() -> None:
    serialized = json.dumps(ANALYSIS_RESPONSE_SCHEMA)

    # Ollama rejects the $defs/$ref form Pydantic emits for nested evidence.
    assert "$ref" not in serialized
    assert "$defs" not in serialized
    assert "anyOf" not in serialized
    item = ANALYSIS_RESPONSE_SCHEMA["properties"]["proposals"]["items"]
    assert item["properties"]["evidence"]["items"]["properties"]["segment_ids"] == {
        "type": "array", "items": {"type": "integer"},
    }
    # The enum must track the model's own kinds rather than a second hand-kept list.
    assert set(item["properties"]["kind"]["enum"]) == set(PROPOSAL_KINDS)
    assert "session_summary" in PROPOSAL_KINDS


def test_analyzer_sends_the_schema_as_the_ollama_format(monkeypatch, tmp_path) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "message": {"content": json.dumps({"proposals": [{
                    "kind": "scene", "title": "Arrival", "body": "They arrive.",
                    "aliases": [], "confidence": 0.9, "visibility": "player",
                    "evidence": [{"segment_ids": [1], "quote": "we arrive"}],
                }]})},
                "done_reason": "stop", "eval_count": 12,
            }).encode()

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse()

    monkeypatch.setattr("campaign_manager.analysis.urllib.request.urlopen", fake_urlopen)
    settings = replace(_settings(tmp_path), analysis_base_url="http://sharn:11434")

    result, metadata = ollama_analyzer(settings)("prompt", "m", ANALYSIS_RESPONSE_SCHEMA)

    assert captured["body"]["format"] == ANALYSIS_RESPONSE_SCHEMA
    assert captured["body"]["format"] != "json"
    assert [proposal.title for proposal in result.proposals] == ["Arrival"]
    assert metadata["eval_count"] == 12


def test_unusable_proposal_is_dropped_without_losing_the_others() -> None:
    # The float evidence below is what failed Wonderland Session 02 in production:
    # the model cited start/end seconds instead of segment ids.
    content = json.dumps({"proposals": [
        {"kind": "scene", "title": "Rabbit chase", "body": "They give chase.",
         "aliases": [], "confidence": 0.9, "visibility": "player",
         "evidence": [0, 126.43, 128.49]},
        {"kind": "npc", "title": "White Rabbit", "body": "Impatient guide.",
         "aliases": [], "confidence": 0.8, "visibility": "gm",
         "evidence": [{"segment_ids": [7], "quote": "hurry up"}]},
        {"kind": "not_a_kind", "title": "Unusable", "body": "", "aliases": [],
         "confidence": 0.5, "visibility": "gm", "evidence": []},
    ]})

    result, diagnostics = parse_analysis_content(content)

    assert [proposal.title for proposal in result.proposals] == ["Rabbit chase", "White Rabbit"]
    assert diagnostics["dropped_proposals"] == 1
    # The whole number is kept as a segment id; the fractional values are carried
    # as the cited time range rather than becoming bogus segment references.
    evidence = result.proposals[0].evidence[0]
    assert evidence.segment_ids == [0]
    assert evidence.cited_seconds == [126.43, 128.49]


def test_analysis_completes_when_some_chunks_fail(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)
    cues = "\n\n".join(
        f"00:00:{index:02d}.000 --> 00:00:{index:02d}.900\nCaelen explores room {index}. "
        + "Details " * 20
        for index in range(30)
    )
    source = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/text",
        headers=headers,
        json={"kind": "transcript", "filename": "long.vtt", "content": f"WEBVTT\n\n{cues}"},
    ).json()
    queued = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis",
        headers=headers, json={"source_artifact_id": source["id"]},
    )
    assert queued.status_code == 202
    calls = []

    def flaky_analyze(prompt, model, schema):
        calls.append(prompt)
        if len(calls) == 1:
            raise ValueError("Ollama returned truncated JSON")
        return AnalysisResult.model_validate({"proposals": [{
            "kind": "scene", "title": f"Scene {len(calls)}", "body": "Something happened.",
            "aliases": [], "confidence": 0.9, "visibility": "player",
            "evidence": [{"segment_ids": [0], "quote": "Caelen explores room 0."}],
        }]}), {"eval_count": 20}

    with session_factory()() as database:
        job = database.scalar(select(Job).where(Job.kind == "analysis"))
        settings = replace(_settings(tmp_path), analysis_chunk_chars=2_200)
        process_analysis_job(database, settings, job, flaky_analyze)

        assert len(calls) > 1
        progress = job.payload["analysis_progress"]
        assert progress["stage"] == "complete"
        assert progress["failed_chunks"] == 1
        assert job.payload["chunk_failures"][0]["chunk_index"] == 0
        proposals = database.scalars(select(AnalysisProposal).where(
            AnalysisProposal.session_id == job.session_id
        )).all()
        assert proposals


def test_consolidation_separates_story_from_meta_and_merges_entities() -> None:
    session = GameSession(
        title="Test", description="", campaign_id=uuid.uuid4(), created_by_id=uuid.uuid4()
    )
    candidates = [ExtractedProposal.model_validate({
        "kind": "player_character", "title": title, "body": body,
        "aliases": [], "confidence": 0.9, "visibility": "player",
        "evidence": [{"segment_ids": [segment], "quote": body}],
    }) for title, body, segment in [
        ("Magnus Heartsbane", "Magnus is a purple tiefling bard.", 1),
        ("Magnus's performance", "Magnus seeks fame through performance.", 2),
    ]]
    candidates.append(ExtractedProposal.model_validate({
        "kind": "follow_up", "title": "Check inspiration rule", "body": "GM will check later.",
        "aliases": [], "confidence": 0.8, "visibility": "gm",
        "evidence": [{"segment_ids": [3], "quote": "I'll check later."}],
    }))
    captured = []

    def fake_analyze(prompt, model, schema):
        captured.append(prompt)
        if "narrative section" in prompt:
            proposals = [{
                "kind": "session_summary", "title": "Session recap", "body": "Magnus performed.",
                "aliases": [], "confidence": 0.9, "visibility": "player",
                "evidence": [{"segment_ids": [1, 2], "quote": "Magnus performed."}],
            }]
        elif "entities section" in prompt:
            proposals = [{
                "kind": "player_character", "title": "Magnus Heartsbane",
                "body": "A purple tiefling bard who seeks fame through performance.",
                "aliases": [], "confidence": 0.9, "visibility": "player",
                "evidence": [{"segment_ids": [1, 2], "quote": "purple tiefling bard"}],
            }]
        else:
            proposals = [{
                "kind": "follow_up", "title": "Check inspiration rule",
                "body": "Confirm the inspiration rule after the session.", "aliases": [],
                "confidence": 0.8, "visibility": "gm",
                "evidence": [{"segment_ids": [3], "quote": "I'll check later."}],
            }]
        return AnalysisResult.model_validate({"proposals": proposals}), {"eval_count": 30}

    result, metadata = consolidate_analysis(
        session, [], [], candidates, fake_analyze, "test-model", 20_000
    )

    assert [proposal.title for proposal in result.proposals].count("Magnus Heartsbane") == 1
    assert next(p for p in result.proposals if p.kind == "follow_up").lane == "meta"
    assert next(p for p in result.proposals if p.kind == "session_summary").lane == "story"
    # Narrative, its coverage retry, entities, then meta; threads has no candidates.
    assert len(captured) == 4
    assert sum("COVERAGE REQUIREMENT" in prompt for prompt in captured) == 1
    assert all("Speakers are not automatically their PCs" in prompt for prompt in captured)
    assert {item["section"] for item in metadata} == {"narrative", "entities", "meta"}


def test_narrative_coverage_retry_replaces_the_narrow_recap() -> None:
    session = GameSession(
        title="Test", description="", campaign_id=uuid.uuid4(), created_by_id=uuid.uuid4()
    )
    candidates = [ExtractedProposal.model_validate({
        "kind": "scene", "title": f"Scene {segment}", "body": f"Something happens at {segment}.",
        "aliases": [], "confidence": 0.9, "visibility": "player",
        "evidence": [{"segment_ids": [segment], "quote": f"line {segment}"}],
    }) for segment in (1, 20, 40, 60, 80)]
    narrow = "The party met in the tavern. " * 20
    full = "The party met, travelled, fought, and returned changed. " * 40

    def fake_analyze(prompt, model, schema):
        body = full if "COVERAGE REQUIREMENT" in prompt else narrow
        return AnalysisResult.model_validate({"proposals": [{
            "kind": "session_summary", "title": "Session recap", "body": body,
            "aliases": [], "confidence": 0.9, "visibility": "player",
            "evidence": [{"segment_ids": [1, 40, 80], "quote": "line 1"}],
        }]}), {"eval_count": 30}

    result, metadata = consolidate_analysis(
        session, [], [], candidates, fake_analyze, "test-model", 20_000
    )

    summaries = [proposal for proposal in result.proposals if proposal.kind == "session_summary"]
    assert len(summaries) == 1
    assert full.strip() in summaries[0].body
    assert narrow.strip() not in summaries[0].body
    assert "coverage_retry" in next(
        item for item in metadata if item["section"] == "narrative"
    )


def test_character_classification_uses_campaign_guide_and_removes_annotations() -> None:
    creator_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    magnus = CampaignGuideEntry(
        id=uuid.uuid4(),
        campaign_id=campaign_id, kind="character", canonical_name="Magnus Heartsbane",
        aliases=["Magnus Hartspain"], notes="Player character", visibility="player",
        created_by_id=creator_id,
    )
    mayor = CampaignGuideEntry(
        id=uuid.uuid4(),
        campaign_id=campaign_id, kind="npc", canonical_name="Mayor Bartholomew Nez",
        aliases=["Mayor Nez"], notes="Mayor of Dinah", visibility="gm",
        created_by_id=creator_id,
    )
    proposals = [ExtractedProposal.model_validate({
        "kind": "character", "title": "Magnus Heartsbane (Michael)", "body": "A bard.",
        "aliases": [], "confidence": 0.9, "visibility": "player", "evidence": [],
    }), ExtractedProposal.model_validate({
        "kind": "character", "title": "Mayor Nez (Schlock)", "body": "The mayor.",
        "aliases": [], "confidence": 0.9, "visibility": "gm", "evidence": [],
    }), ExtractedProposal.model_validate({
        "kind": "character", "title": "Unknown Teenager (Prankster)", "body": "A stranger.",
        "aliases": [], "confidence": 0.8, "visibility": "gm", "evidence": [],
    })]

    result = canonicalize_character_kinds(proposals, [magnus, mayor], {magnus.id})

    assert [(proposal.kind, proposal.title) for proposal in result] == [
        ("player_character", "Magnus Heartsbane"),
        ("npc", "Mayor Bartholomew Nez"),
        ("npc", "Unknown Teenager"),
    ]


def test_prompt_includes_resolved_speaker_attribution(tmp_path) -> None:
    session = GameSession(title="Test", description="", campaign_id=uuid.uuid4(), created_by_id=uuid.uuid4())
    prompt, _ = build_analysis_prompt(
        session, [], [{"start": 12.0, "end": 15.0, "speaker_name": "Rob", "text": "I open the door."}], 2_000,
        speaker_context=["Rob plays Caelen (primary); notes=none"],
    )

    assert "Rob: I open the door." in prompt
    assert "Rob plays Caelen (primary)" in prompt
    assert 'Return exactly one JSON object with a "proposals" array' in prompt
    assert "session_summary, player_character, npc, monster" in prompt


def test_analysis_status_is_disabled_by_default(tmp_path) -> None:
    status = ollama_status(replace(_settings(tmp_path), analysis_provider="disabled"))
    assert status == {"configured": False, "ready": False, "model": "qwen3:4b", "models": []}

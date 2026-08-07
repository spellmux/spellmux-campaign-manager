import json
import uuid
from dataclasses import replace

import pytest
from sqlalchemy import select
from test_auth_campaigns import configured_client, create_campaign_and_session, login

from campaign_manager.analysis import (
    ANALYSIS_RESPONSE_SCHEMA,
    PROPOSAL_KINDS,
    SECTIONS,
    AnalysisResult,
    ExtractedProposal,
    _bounded_result,
    _trim_section,
    _unsupported_identity_thread,
    assign_speaker_pseudonyms,
    build_analysis_prompt,
    build_analysis_prompts,
    canonicalize_character_kinds,
    consolidate_analysis,
    is_reserved_speaker_name,
    merge_chunk_proposals,
    narration_speaker,
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
        json={"kind": "npc", "canonical_name": "Caelen", "aliases": ["Kalen"], "notes": "Player character", "visibility": "gm"},
    )
    queued = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis",
        headers=headers, json={"source_artifact_id": source["id"]},
    ).json()
    captured = {}

    def fake_analyze(prompt, model, schema):
        captured.update(prompt=prompt, model=model, schema=schema)
        return AnalysisResult.model_validate({"proposals": [{
            "kind": "npc", "title": "Caelen", "body": "Opened the door.",
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
    assert "npc: Caelen" in captured["prompt"]
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
    # Comfortably above the fixed rules prefix so this exercises source truncation
    # rather than failing outright when the rules text grows.
    max_chars = 3_000
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

    # The budget must leave room for enough segments per chunk that overlap applies;
    # build_analysis_prompts scales overlap down to zero for very short chunks.
    prompts = build_analysis_prompts(session, [], segments, max_chars=4_000, overlap_segments=2)

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
        "kind": "npc", "title": "Caelen", "body": "Opened the door.",
        "aliases": [], "confidence": 0.8, "visibility": "player",
        "evidence": [{"segment_ids": [4], "quote": "I open the door."}],
    })
    second = ExtractedProposal.model_validate({
        "kind": "npc", "title": "caelen", "body": "Found the key.",
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
        if "recap section" in prompt:
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
    assert all("Speakers are not automatically their PCs" in prompt for prompt in captured)
    # Sections with no candidates are skipped, so scenes and threads do not run here.
    assert {item["section"] for item in metadata} == {"recap", "entities", "meta"}


def test_each_section_may_only_return_its_own_kinds() -> None:
    # The observed failure: told in prose to return one recap and no entities, the
    # model returned eleven scenes, several entities, and no recap. The grammar now
    # makes that unexpressible, so assert the schema handed to each section.
    seen: dict[str, list[str]] = {}

    def fake_analyze(prompt, model, schema):
        enum = schema["properties"]["proposals"]["items"]["properties"]["kind"]["enum"]
        section = next(s.name for s in SECTIONS if s.instruction[:40] in prompt)
        seen[section] = enum
        return AnalysisResult.model_validate({"proposals": [{
            "kind": enum[0], "title": f"{section} entry", "body": "Body.",
            "aliases": [], "confidence": 0.9, "visibility": "gm",
            "evidence": [{"segment_ids": [1], "quote": "q"}],
        }]}), {"eval_count": 5}

    session = GameSession(
        title="Test", description="", campaign_id=uuid.uuid4(), created_by_id=uuid.uuid4())
    candidates = [ExtractedProposal.model_validate({
        "kind": kind, "title": f"{kind} candidate", "body": "Body.", "aliases": [],
        "confidence": 0.8, "visibility": "gm",
        "evidence": [{"segment_ids": [index], "quote": "q"}],
    }) for index, kind in enumerate(
        ["scene", "npc", "quest", "rule", "memorable_moment", "item"])]

    consolidate_analysis(session, [], [], candidates, fake_analyze, "m", 20_000)

    assert seen["recap"] == ["session_summary"], "a recap section must not permit scenes"
    assert "session_summary" not in seen["scenes"]
    assert "session_summary" not in seen["entities"]
    assert set(seen["threads"]) == {"quest", "important_decision", "unresolved_question"}
    assert set(seen["meta"]) == {"rule", "follow_up", "table_note"}


def test_a_section_returning_the_wrong_kinds_cannot_displace_the_recap() -> None:
    # Simulates the real response: scenes where a recap was asked for.
    def wrong_kinds(prompt, model, schema):
        # Always a scene, whatever the section asked for.
        return AnalysisResult.model_validate({"proposals": [{
            "kind": "scene", "title": "Not a recap", "body": "Body.", "aliases": [],
            "confidence": 0.9, "visibility": "gm", "evidence": [],
        }]}), {"eval_count": 5}

    session = GameSession(
        title="Test", description="", campaign_id=uuid.uuid4(), created_by_id=uuid.uuid4())
    candidates = [ExtractedProposal.model_validate({
        "kind": "scene", "title": f"Scene {i}", "body": "Body.", "aliases": [],
        "confidence": 0.8, "visibility": "gm",
        "evidence": [{"segment_ids": [i], "quote": "q"}],
    }) for i in range(6)]

    result, metadata = consolidate_analysis(
        session, [], [], candidates, wrong_kinds, "m", 20_000)

    # A scene returned for the recap section is discarded, and the shortfall is
    # recorded rather than the run silently completing without a recap.
    recap_meta = next(m for m in metadata if m["section"] == "recap")
    assert recap_meta.get("missing_required_section") == "recap"
    assert not any(p.kind == "session_summary" for p in result.proposals)


def test_trimming_never_discards_a_required_kind() -> None:
    recap = ExtractedProposal.model_validate({
        "kind": "session_summary", "title": "Session recap", "body": "Recap.",
        "aliases": [], "confidence": 0.1, "visibility": "player", "evidence": []})
    filler = [ExtractedProposal.model_validate({
        "kind": "scene", "title": f"Scene {i}", "body": "Body.", "aliases": [],
        "confidence": 0.99, "visibility": "player", "evidence": []}) for i in range(60)]

    # The recap has the lowest confidence and arrives first, which under positional
    # or confidence-only trimming is exactly how it used to be lost.
    bounded = _bounded_result([recap, *filler])

    assert len(bounded) <= 40
    assert any(p.kind == "session_summary" for p in bounded)


def test_scene_trimming_keeps_the_earliest_scenes_in_order() -> None:
    section = next(s for s in SECTIONS if s.name == "scenes")
    scenes = [ExtractedProposal.model_validate({
        "kind": "scene", "title": f"Scene at {start}", "body": "Body.", "aliases": [],
        # Later scenes are the most confident, so confidence ordering would drop
        # the opening of the session.
        "confidence": 0.5 + index / 100,
        "visibility": "player",
        "evidence": [{"segment_ids": [start], "quote": "q"}],
    }) for index, start in enumerate(range(0, 200, 10))]

    trimmed = _trim_section(scenes, section)

    assert len(trimmed) == section.maximum
    starts = [p.evidence[0].segment_ids[0] for p in trimmed]
    assert starts == sorted(starts), "an outline must stay chronological"
    assert starts[0] == 0, "the opening scene must survive"

def test_character_classification_uses_campaign_guide_and_removes_annotations() -> None:
    creator_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    magnus = CampaignGuideEntry(
        id=uuid.uuid4(),
        campaign_id=campaign_id, kind="npc", canonical_name="Magnus Heartsbane",
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
        "kind": "npc", "title": "Magnus Heartsbane (Michael)", "body": "A bard.",
        "aliases": [], "confidence": 0.9, "visibility": "player", "evidence": [],
    }), ExtractedProposal.model_validate({
        "kind": "npc", "title": "Mayor Nez (Schlock)", "body": "The mayor.",
        "aliases": [], "confidence": 0.9, "visibility": "gm", "evidence": [],
    }), ExtractedProposal.model_validate({
        "kind": "npc", "title": "Unknown Teenager (Prankster)", "body": "A stranger.",
        "aliases": [], "confidence": 0.8, "visibility": "gm", "evidence": [],
    })]

    result = canonicalize_character_kinds(proposals, [magnus, mayor], {magnus.id})

    assert [(proposal.kind, proposal.title) for proposal in result] == [
        ("player_character", "Magnus Heartsbane"),
        ("npc", "Mayor Bartholomew Nez"),
        ("npc", "Unknown Teenager"),
    ]


def test_prompt_attributes_lines_to_characters_and_withholds_player_names(tmp_path) -> None:
    session = GameSession(title="Test", description="", campaign_id=uuid.uuid4(), created_by_id=uuid.uuid4())
    segments = [
        # A speaker resolved to a character: narrate as the character.
        {"start": 12.0, "end": 15.0, "speaker": "SPEAKER_00", "speaker_name": "Rob",
         "speaker_profile_id": "p1", "character_name": "Caelen", "text": "I open the door."},
        # A known human with no character is the GM voicing the world.
        {"start": 16.0, "end": 18.0, "speaker": "SPEAKER_01", "speaker_name": "Rob's friend",
         "speaker_profile_id": "p2", "text": "The door creaks open."},
        # Unreviewed clusters must never surface their raw label, but must stay
        # distinguishable from each other.
        {"start": 19.0, "end": 21.0, "speaker": "SPEAKER_02", "text": "Someone laughs."},
        {"start": 22.0, "end": 24.0, "speaker": "SPEAKER_03", "text": "A different voice."},
        {"start": 25.0, "end": 27.0, "speaker": "SPEAKER_02", "text": "The first voice again."},
    ]
    assign_speaker_pseudonyms(segments)

    prompt, _ = build_analysis_prompt(session, [], segments, 4_000)

    assert "Caelen: I open the door." in prompt
    assert "GM: The door creaks open." in prompt
    # Stable per cluster, and distinct between clusters.
    assert "Speaker A: Someone laughs." in prompt
    assert "Speaker B: A different voice." in prompt
    assert "Speaker A: The first voice again." in prompt
    # Real names and raw cluster labels are the source of player/character mix-ups.
    assert "Rob" not in prompt
    assert "SPEAKER_0" not in prompt
    assert 'Return exactly one JSON object with a "proposals" array' in prompt
    assert "kinds: session_summary, player_character, npc," in prompt
    # A named individual is an npc; only a type of creature is a creature.
    assert '"Bob the Mock Turtle" is an npc, "Mock Turtle" is a creature' in prompt


def _guide_entry(kind: str, name: str, aliases: list[str], campaign_id: uuid.UUID):
    return CampaignGuideEntry(
        id=uuid.uuid4(), campaign_id=campaign_id, kind=kind, canonical_name=name,
        aliases=aliases, notes="", visibility="gm", created_by_id=uuid.uuid4(),
    )


def test_identity_guards_derive_from_campaign_data_not_hardcoded_names() -> None:
    # A different campaign with different characters must get identical treatment,
    # so every guard is driven by guide entries rather than known names.
    campaign_id = uuid.uuid4()
    vess = _guide_entry("player_character", "Vess Aldermoor", ["Vess", "Aldermoor"], campaign_id)
    kip = _guide_entry("player_character", "Kip", [], campaign_id)
    guide = [vess, kip]

    # Alias-aware: "Aldermoor" and "Kip" name two PCs even though neither
    # canonical name appears in full.
    merged = ExtractedProposal.model_validate({
        "kind": "unresolved_question", "title": "Is Aldermoor secretly Kip?",
        "body": "The innkeeper implies they are the same.", "aliases": [],
        "confidence": 0.6, "visibility": "gm", "evidence": [],
    })
    assert _unsupported_identity_thread(merged, guide) is True

    # Explicit identity language is legitimate and must survive.
    supported = ExtractedProposal.model_validate({
        "kind": "unresolved_question", "title": "Aldermoor revealed to be Kip",
        "body": "Vess is actually Kip, revealed to be the same person.", "aliases": [],
        "confidence": 0.9, "visibility": "gm", "evidence": [],
    })
    assert _unsupported_identity_thread(supported, guide) is False

    # Word boundaries: a short PC name must not match inside another word.
    # "Kip" appears inside "Kipling", which previously tripped the guard.
    incidental = ExtractedProposal.model_validate({
        "kind": "unresolved_question", "title": "Who owns the Kipling estate?",
        "body": "Vess Aldermoor asks about the Kipling estate.", "aliases": [],
        "confidence": 0.5, "visibility": "gm", "evidence": [],
    })
    assert _unsupported_identity_thread(incidental, guide) is False


def test_non_speech_clusters_are_not_attributed_to_the_game_master() -> None:
    # A reviewed music or crosstalk cluster carries a display name but no person.
    music = {"start": 1.0, "end": 4.0, "speaker": "SPEAKER_05", "speaker_name": "Music",
             "speaker_disposition": "music", "text": "la la la"}
    crosstalk = {"start": 5.0, "end": 6.0, "speaker": "SPEAKER_04",
                 "speaker_name": "Needs attention", "speaker_disposition": "crosstalk",
                 "text": "indistinct"}
    gm = {"start": 7.0, "end": 8.0, "speaker": "SPEAKER_00", "speaker_name": "Tim",
          "speaker_profile_id": "p0", "speaker_disposition": "confirmed",
          "text": "The door creaks."}

    assert narration_speaker(music) == "Music"
    assert narration_speaker(crosstalk) == "Needs attention"
    assert narration_speaker(gm) == "GM"
    # None of these may become an entity.
    assert is_reserved_speaker_name("Music") is True
    assert is_reserved_speaker_name("Needs attention") is True
    assert is_reserved_speaker_name("Caelen Myrhart") is False


def test_reserved_speaker_roles_never_become_entities() -> None:
    campaign_id = uuid.uuid4()
    guide = [_guide_entry("player_character", "Vess Aldermoor", ["Vess"], campaign_id)]
    proposals = [ExtractedProposal.model_validate({
        "kind": kind, "title": title, "body": "Spoke during the session.",
        "aliases": [], "confidence": 0.8, "visibility": "gm", "evidence": [],
    }) for kind, title in [
        ("npc", "GM"),
        ("npc", "GM (Host)"),
        ("player_character", "SPEAKER_01 (Kip)"),
        ("npc", "Unidentified speaker"),
        ("npc", "Speaker B"),
        ("npc", "Brannock the Smith"),
    ]]

    result = canonicalize_character_kinds(proposals, guide, {guide[0].id})

    # Only the real in-fiction NPC survives.
    assert [proposal.title for proposal in result] == ["Brannock the Smith"]


def test_aliases_belonging_to_another_entity_are_dropped() -> None:
    campaign_id = uuid.uuid4()
    guide = [
        _guide_entry("npc", "Hollow Empress", ["Empress Nihil"], campaign_id),
        _guide_entry("player_character", "Vess Aldermoor", ["Vess"], campaign_id),
    ]
    proposal = ExtractedProposal.model_validate({
        "kind": "player_character", "title": "Vess Aldermoor",
        "body": "A ranger.", "confidence": 0.9, "visibility": "player", "evidence": [],
        # "Empress Nihil" belongs to a different entity; "Vessa" is a new spelling.
        "aliases": ["Vess", "Empress Nihil", "Vessa", "GM"],
    })

    result = canonicalize_character_kinds([proposal], guide, {guide[1].id})

    assert result[0].aliases == ["Vess", "Vessa"]


def test_analysis_status_is_disabled_by_default(tmp_path) -> None:
    status = ollama_status(replace(_settings(tmp_path), analysis_provider="disabled"))
    assert status == {"configured": False, "ready": False, "model": "qwen3:4b", "models": []}


def test_a_paragraph_per_entry_recap_merges_into_one_ordered_recap() -> None:
    # The live model answers the recap section with one entry per paragraph. Each
    # merges into a single recap, so they must all survive and stay in order.
    session = GameSession(
        title="Test", description="", campaign_id=uuid.uuid4(), created_by_id=uuid.uuid4())
    paragraphs = ["The session opened.", "Then combat.", "Then a decision.", "It ended."]

    def paragraphs_per_entry(prompt, model, schema):
        enum = schema["properties"]["proposals"]["items"]["properties"]["kind"]["enum"]
        if enum != ["session_summary"]:
            return AnalysisResult(proposals=[]), {"eval_count": 1}
        return AnalysisResult.model_validate({"proposals": [{
            "kind": "session_summary", "title": "Session recap", "body": body,
            "aliases": [], "confidence": 0.5 + index / 100, "visibility": "player",
            "evidence": [{"segment_ids": [index * 10], "quote": "q"}],
        } for index, body in enumerate(paragraphs)]}), {"eval_count": 9}

    candidates = [ExtractedProposal.model_validate({
        "kind": "scene", "title": f"Scene {i}", "body": "Body.", "aliases": [],
        "confidence": 0.8, "visibility": "player",
        "evidence": [{"segment_ids": [i * 10], "quote": "q"}],
    }) for i in range(6)]

    result, _metadata = consolidate_analysis(
        session, [], [], candidates, paragraphs_per_entry, "m", 20_000)

    recaps = [p for p in result.proposals if p.kind == "session_summary"]
    assert [p.body for p in recaps] == paragraphs, "all paragraphs, in narrative order"

    # merge_chunk_proposals keys every session_summary together, so the reviewed
    # finding is one recap containing each paragraph once.
    segments = list(enumerate([{"text": "t", "start": float(i), "end": float(i) + 1}
                               for i in range(80)]))
    merged = merge_chunk_proposals([(result.proposals, segments)])
    bodies = [p.body for p, _ev in merged if p.kind == "session_summary"]
    assert len(bodies) == 1
    for paragraph in paragraphs:
        assert paragraph in bodies[0]


def test_candidate_budget_tracks_transcript_room_not_chunk_size() -> None:
    from campaign_manager.analysis import candidate_budget

    # The density the 16k chunk produced: ~9,000 characters of transcript, 8 findings.
    assert candidate_budget(9_000) == 8
    # A chunk that holds three times the transcript asks for proportionally more,
    # up to the ceiling that keeps one response from being truncated.
    assert candidate_budget(25_000) == 20
    # Tiny chunks still ask for a useful minimum rather than zero.
    assert candidate_budget(100) == 8


def test_prompt_asks_for_more_candidates_when_the_chunk_holds_more_transcript() -> None:
    session = GameSession(title="Test", description="", campaign_id=uuid.uuid4(), created_by_id=uuid.uuid4())
    segments = [{"start": i, "end": i + 1, "text": "x" * 60} for i in range(3_000)]

    def asked_for(limit: int) -> int:
        prompt, _ = build_analysis_prompt(session, [], segments, limit)
        line = next(line for line in prompt.splitlines() if "Extract at most" in line)
        return int(line.split("at most ")[1].split()[0])

    small, large = asked_for(16_000), asked_for(44_000)
    # The exact numbers depend on how much room the guide leaves, which is what
    # candidate_budget is unit-tested for; what matters here is that a chunk holding
    # more transcript asks for more, and that the ceiling still applies.
    assert small < large <= 20
    # The placeholder must never survive into the prompt.
    prompt, _ = build_analysis_prompt(session, [], segments, 16_000)
    assert "__CANDIDATE_BUDGET__" not in prompt


def test_near_duplicate_titles_merge_into_one_finding() -> None:
    from campaign_manager.analysis import merge_key_title

    # A real run produced these three as separate unresolved questions.
    variants = [
        "Moth CR and Threat Level Discrepancy",
        "Moth CR and Threat Level Discrepancy (Contextual Note)",
        "Moth CR and Threat Level Discrepancy (Follow-up)",
    ]
    assert len({merge_key_title(title) for title in variants}) == 1

    included = [(0, {"start": 1.0, "end": 2.0, "text": "The moths hit hard."})]
    proposals = [
        ExtractedProposal.model_validate({
            "kind": "unresolved_question", "title": title, "body": f"body for {index}",
            "aliases": [], "confidence": 0.5, "visibility": "gm",
            "evidence": [{"segment_ids": [0], "quote": "The moths hit hard."}],
        })
        for index, title in enumerate(variants)
    ]
    merged = merge_chunk_proposals([(proposals, included)])
    assert len(merged) == 1
    # Nothing is thrown away: each variant's wording is kept in the survivor.
    body = merged[0][0].body
    assert all(f"body for {index}" in body for index in range(3))

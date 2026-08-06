"""Each analysis is a generation of findings, and only one is live at a time."""

import uuid

from sqlalchemy import select
from test_analysis import _settings
from test_auth_campaigns import configured_client, create_campaign_and_session, login

from campaign_manager.analysis import AnalysisResult, process_analysis_job
from campaign_manager.database import session_factory
from campaign_manager.jobs import complete_job, fail_job
from campaign_manager.models import AnalysisProposal, AnalysisRun, GameSession, Job


def analyze_returning(*findings: tuple[str, str]):
    def analyze(prompt, model, schema):
        return AnalysisResult.model_validate({"proposals": [{
            "kind": "npc", "title": title, "body": body,
            "aliases": [], "confidence": 0.9, "visibility": "player",
            "evidence": [{"segment_ids": [0], "quote": "Caelen opens the door."}],
        } for title, body in findings]}), {"eval_count": 7}

    return analyze


def run_analysis(client, headers, campaign_id, session_id, source_id, analyze, tmp_path):
    queued = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis",
        headers=headers, json={"source_artifact_id": source_id},
    ).json()
    with session_factory()() as database:
        job = database.get(Job, uuid.UUID(queued["id"]))
        process_analysis_job(database, _settings(tmp_path), job, analyze)
        run_id = str(job.payload["analysis_run_id"])
        # The worker, not the analysis code, closes the job; queueing another
        # analysis is refused while one is still queued or running.
        complete_job(database, job)
        return run_id


def session_with_transcript(client, headers, tmp_path):
    campaign_id, session_id = create_campaign_and_session(client, headers)
    source = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/text",
        headers=headers,
        json={
            "kind": "transcript", "filename": "session.vtt",
            "content": "WEBVTT\n\n00:00:12.000 --> 00:00:16.000\nCaelen opens the door.",
        },
    ).json()
    return campaign_id, session_id, source["id"]


def test_re_analysis_supersedes_the_previous_generation_without_destroying_it(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id, source_id = session_with_transcript(client, headers, tmp_path)

    first_run = run_analysis(
        client, headers, campaign_id, session_id, source_id,
        analyze_returning(("Caelen", "Opened the door.")), tmp_path,
    )
    second_run = run_analysis(
        client, headers, campaign_id, session_id, source_id,
        analyze_returning(("Caelen", "Opened the door, having picked the lock.")), tmp_path,
    )
    assert first_run != second_run

    # The queue shows one generation, not both.
    queue = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals",
        headers=headers,
    ).json()
    assert [item["body"] for item in queue] == ["Opened the door, having picked the lock."]
    campaign_queue = client.get(
        f"/api/v1/campaigns/{campaign_id}/analysis-proposals", headers=headers
    ).json()
    assert len(campaign_queue) == 1

    # The superseded generation is kept, so a disappointing re-run can be undone.
    with session_factory()() as database:
        assert database.scalar(select(GameSession.active_analysis_run_id).where(
            GameSession.id == uuid.UUID(session_id)
        )) == uuid.UUID(second_run)
        assert len(database.scalars(select(AnalysisProposal)).all()) == 2
        runs = database.scalars(select(AnalysisRun).order_by(AnalysisRun.created_at)).all()
        assert [run.status for run in runs] == ["succeeded", "succeeded"]
        assert [run.finding_count for run in runs] == [1, 1]

    runs = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-runs", headers=headers
    ).json()
    assert [run["id"] for run in runs] == [second_run, first_run]
    assert [run["is_active"] for run in runs] == [True, False]

    restored = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-runs/{first_run}/activate",
        headers=headers,
    )
    assert restored.status_code == 200
    assert restored.json()["is_active"] is True
    assert [item["body"] for item in client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals", headers=headers
    ).json()] == ["Opened the door."]


def test_superseded_findings_cannot_be_approved_or_published(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id, source_id = session_with_transcript(client, headers, tmp_path)

    # The first generation has one finding approved and one left unreviewed.
    run_analysis(
        client, headers, campaign_id, session_id, source_id,
        analyze_returning(
            ("Caelen", "Opened the door."),
            ("The Duchess", "Shouted from the next room."),
        ),
        tmp_path,
    )
    first_generation = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals", headers=headers
    ).json()
    approved_then_superseded = next(i for i in first_generation if i["title"] == "Caelen")
    stale = next(i for i in first_generation if i["title"] == "The Duchess")
    client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}"
        f"/analysis-proposals/{approved_then_superseded['id']}/approve",
        headers=headers,
    )
    run_analysis(
        client, headers, campaign_id, session_id, source_id,
        analyze_returning(("Caelen", "Opened the door, having picked the lock.")), tmp_path,
    )
    fresh = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals", headers=headers
    ).json()[0]
    assert fresh["title"] == "Caelen"

    # A superseded finding is not reviewable, even by id.
    refused = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{stale['id']}/approve",
        headers=headers,
    )
    assert refused.status_code == 409
    assert "superseded" in refused.json()["detail"]

    # A draft built now must not contain the old generation's approved text, which
    # is exactly how a stale summary used to reach a player-facing page.
    client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{fresh['id']}/approve",
        headers=headers,
    )
    draft = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/publications",
        headers=headers, json={},
    ).json()
    assert "having picked the lock" in draft["content"]
    assert draft["content"].count("Caelen") == 1


def test_model_findings_are_immutable_but_manual_ones_are_not(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id, source_id = session_with_transcript(client, headers, tmp_path)
    run_analysis(
        client, headers, campaign_id, session_id, source_id,
        analyze_returning(("Caelen", "Opened the door.")), tmp_path,
    )
    finding = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals", headers=headers
    ).json()[0]
    refused = client.put(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{finding['id']}",
        headers=headers,
        json={"title": "Caelen", "body": "Rewritten by hand.", "aliases": [], "visibility": "gm"},
    )
    assert refused.status_code == 409
    assert "immutable" in refused.json()["detail"]

    # A hand-authored finding has no run and stays editable.
    manual = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals",
        headers=headers,
        json={
            "kind": "npc", "title": "The Dormouse", "body": "Asleep in the teapot.",
            "aliases": [], "evidence": [], "confidence": None, "visibility": "gm",
            "provider": "manual", "model": "", "run_metadata": {},
        },
    ).json()
    edited = client.put(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{manual['id']}",
        headers=headers,
        json={"title": "The Dormouse", "body": "Asleep in the teapot, snoring.", "aliases": [], "visibility": "gm"},
    )
    assert edited.status_code == 200
    assert edited.json()["body"] == "Asleep in the teapot, snoring."
    # Manual findings belong to every generation, so the queue keeps showing it.
    assert "The Dormouse" in [item["title"] for item in client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals", headers=headers
    ).json()]


def test_chronicle_edits_are_canon_and_survive_re_analysis(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id, source_id = session_with_transcript(client, headers, tmp_path)
    run_analysis(
        client, headers, campaign_id, session_id, source_id,
        analyze_returning(("Caelen", "Opened the door.")), tmp_path,
    )
    first = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals", headers=headers
    ).json()[0]
    client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{first['id']}/approve",
        headers=headers,
    )
    entry = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/chronicle", headers=headers
    ).json()[0]
    assert entry["edited_at"] is None

    edited = client.put(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/chronicle/{entry['id']}",
        headers=headers,
        json={
            "section": entry["section"], "entry_type": entry["entry_type"], "title": "Caelen",
            "body": "Caelen forced the door with a shoulder.", "position": entry["position"],
            "visibility": "player",
        },
    ).json()
    assert edited["edited_at"] is not None

    # Re-analyse and approve the new finding for the same entity.
    run_analysis(
        client, headers, campaign_id, session_id, source_id,
        analyze_returning(("Caelen", "Opened the door, having picked the lock.")), tmp_path,
    )
    second = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals", headers=headers
    ).json()[0]
    approved = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{second['id']}/approve",
        headers=headers,
    )
    assert approved.status_code == 200

    chronicle = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/chronicle", headers=headers
    ).json()
    # One entry, still the human wording: the hand edit is the campaign's canon.
    assert len(chronicle) == 1
    assert chronicle[0]["body"] == "Caelen forced the door with a shoulder."


def test_untouched_chronicle_text_is_refreshed_by_a_later_run(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id, source_id = session_with_transcript(client, headers, tmp_path)
    run_analysis(
        client, headers, campaign_id, session_id, source_id,
        analyze_returning(("Caelen", "Opened the door.")), tmp_path,
    )
    first = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals", headers=headers
    ).json()[0]
    client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{first['id']}/approve",
        headers=headers,
    )
    run_analysis(
        client, headers, campaign_id, session_id, source_id,
        analyze_returning(("Caelen", "Opened the door, having picked the lock.")), tmp_path,
    )
    second = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals", headers=headers
    ).json()[0]
    client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{second['id']}/approve",
        headers=headers,
    )

    chronicle = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/chronicle", headers=headers
    ).json()
    # No duplicate entry, and nobody had edited the machine text, so it is replaced.
    assert len(chronicle) == 1
    assert chronicle[0]["body"] == "Opened the door, having picked the lock."
    assert chronicle[0]["source_proposal_id"] == second["id"]


def test_failed_run_leaves_the_previous_generation_active(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id, source_id = session_with_transcript(client, headers, tmp_path)
    good_run = run_analysis(
        client, headers, campaign_id, session_id, source_id,
        analyze_returning(("Caelen", "Opened the door.")), tmp_path,
    )

    def returns_nothing(prompt, model, schema):
        return AnalysisResult(proposals=[]), {"done_reason": "stop"}

    queued = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis",
        headers=headers, json={"source_artifact_id": source_id},
    ).json()
    with session_factory()() as database:
        job = database.get(Job, uuid.UUID(queued["id"]))
        try:
            process_analysis_job(database, _settings(tmp_path), job, returns_nothing)
        except ValueError as exc:
            fail_job(database, job, str(exc))
        assert database.scalar(select(GameSession.active_analysis_run_id).where(
            GameSession.id == uuid.UUID(session_id)
        )) == uuid.UUID(good_run)
        statuses = {
            str(run.id): run.status
            for run in database.scalars(select(AnalysisRun)).all()
        }
        assert statuses[good_run] == "succeeded"
        assert sorted(statuses.values()) == ["failed", "succeeded"]

    # The reviewable generation is untouched by the failure.
    assert [item["body"] for item in client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals", headers=headers
    ).json()] == ["Opened the door."]

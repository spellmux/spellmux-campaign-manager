import json
import uuid

from sqlalchemy import select
from test_auth_campaigns import configured_client, create_campaign_and_session, login

from campaign_manager.database import session_factory
from campaign_manager.models import Artifact, User


def test_sessions_are_ordered_by_session_date_with_undated_last(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, _session_id = create_campaign_and_session(client, headers)
    for title, session_date in (("Earlier", "2025-01-02"), ("Undated", None), ("Middle", "2026-01-02")):
        response = client.post(
            f"/api/v1/campaigns/{campaign_id}/sessions",
            headers=headers,
            json={"title": title, "session_date": session_date},
        )
        assert response.status_code == 201

    sessions = client.get(f"/api/v1/campaigns/{campaign_id}/sessions", headers=headers).json()
    assert [item["title"] for item in sessions] == [
        "Earlier", "Middle", "Arrival at the Crossroads", "Undated",
    ]


def test_session_guide_speaker_and_text_source_are_manageable(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)

    session = client.put(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}",
        headers=headers,
        json={"title": "Edited session", "session_date": "2026-07-30", "description": "Recap"},
    )
    guide = client.post(
        f"/api/v1/campaigns/{campaign_id}/guide",
        headers=headers,
        json={"kind": "character", "canonical_name": "Kalen", "aliases": [], "notes": "", "visibility": "gm"},
    ).json()
    edited_guide = client.put(
        f"/api/v1/campaigns/{campaign_id}/guide/{guide['id']}",
        headers=headers,
        json={"kind": "character", "canonical_name": "Caelen", "aliases": ["Kalen"], "notes": "PC", "visibility": "player"},
    )
    speaker = client.post(
        f"/api/v1/campaigns/{campaign_id}/speakers",
        headers=headers,
        json={"display_name": "Rob", "notes": "GM"},
    ).json()
    edited_speaker = client.put(
        f"/api/v1/campaigns/{campaign_id}/speakers/{speaker['id']}",
        headers=headers,
        json={"display_name": "Robert", "notes": "Game Master"},
    )
    source = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/text",
        headers=headers,
        json={"kind": "notes", "filename": "notes.md", "content": "First draft"},
    ).json()
    edited_source = client.put(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/artifacts/{source['id']}",
        headers=headers,
        json={"filename": "recap.md", "content": "Second draft"},
    )

    assert session.json()["description"] == "Recap"
    assert edited_guide.json()["canonical_name"] == "Caelen"
    assert edited_speaker.json()["display_name"] == "Robert"
    assert edited_source.json()["original_filename"] == "recap.md"
    assert client.delete(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/artifacts/{source['id']}",
        headers=headers,
    ).status_code == 204
    assert client.delete(
        f"/api/v1/campaigns/{campaign_id}/guide/{guide['id']}", headers=headers
    ).status_code == 204
    assert client.delete(
        f"/api/v1/campaigns/{campaign_id}/speakers/{speaker['id']}", headers=headers
    ).status_code == 204


def test_comparison_endpoint_aligns_native_transcript_with_vtt(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)
    source = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/text",
        headers=headers,
        json={
            "kind": "transcript",
            "filename": "source.vtt",
            "content": "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nKaelin enters.\n",
        },
    ).json()
    relative = f"{campaign_id}/{session_id}/transcript/native.json"
    path = tmp_path / "artifacts" / relative
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"segments": [{"id": 0, "start": 1, "end": 3, "text": "Caelen enters."}]}),
        encoding="utf-8",
    )
    with session_factory()() as database:
        user = database.scalar(select(User))
        database.add(Artifact(
            session_id=uuid.UUID(session_id), kind="raw_transcript", relative_path=relative,
            original_filename="native.json", media_type="application/json", size_bytes=path.stat().st_size,
            sha256="2" * 64, visibility="gm", created_by_id=user.id,
        ))
        database.commit()

    compared = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/comparisons/{source['id']}",
        headers=headers,
    )

    assert compared.status_code == 200
    assert compared.json()["similarity"] == 0.5
    assert any(item["kind"] == "replace" for item in compared.json()["passages"])

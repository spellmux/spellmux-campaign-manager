from test_auth_campaigns import configured_client, create_campaign_and_session, login


def create_proposal(client, headers, campaign_id, session_id, **overrides):
    payload = {
        "kind": "npc",
        "title": "Caelen",
        "body": "A traveler caught between worlds.",
        "aliases": ["Kalen"],
        "evidence": [{"quote": "Caelen opens the door.", "start_seconds": 12, "end_seconds": 16}],
        "confidence": 0.87,
        "visibility": "gm",
        "provider": "test-provider",
        "model": "test-model",
        "run_metadata": {"run_id": "run-1"},
    }
    payload.update(overrides)
    return client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals",
        headers=headers,
        json=payload,
    )


def test_proposal_can_be_edited_approved_and_promoted(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)

    created = create_proposal(client, headers, campaign_id, session_id)
    assert created.status_code == 201
    proposal = created.json()
    assert proposal["status"] == "proposed"
    assert proposal["evidence"][0]["start_seconds"] == 12

    edited = client.put(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{proposal['id']}",
        headers=headers,
        json={
            "title": "Caelen",
            "body": "An elven traveler caught between worlds.",
            "aliases": ["Kalen", " Kalen "],
            "visibility": "player",
        },
    )
    assert edited.status_code == 200
    assert edited.json()["aliases"] == ["Kalen"]

    approved = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{proposal['id']}/approve",
        headers=headers,
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["promoted_guide_entry_id"] is not None

    guide = client.get(f"/api/v1/campaigns/{campaign_id}/guide", headers=headers).json()
    assert [(entry["kind"], entry["canonical_name"]) for entry in guide] == [("npc", "Caelen")]
    assert guide[0]["notes"] == "An elven traveler caught between worlds."
    assert client.put(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{proposal['id']}",
        headers=headers,
        json={"title": "Changed", "body": "", "aliases": [], "visibility": "gm"},
    ).status_code == 409


def test_approval_links_exact_guide_match_without_overwriting_and_summary_stays_session_only(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)
    existing = client.post(
        f"/api/v1/campaigns/{campaign_id}/guide",
        headers=headers,
        json={"kind": "npc", "canonical_name": "Caelen", "aliases": [], "notes": "Curated truth", "visibility": "gm"},
    ).json()
    duplicate = create_proposal(client, headers, campaign_id, session_id).json()
    approved = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{duplicate['id']}/approve",
        headers=headers,
    ).json()
    assert approved["promoted_guide_entry_id"] == existing["id"]
    guide = client.get(f"/api/v1/campaigns/{campaign_id}/guide", headers=headers).json()
    assert len(guide) == 1
    assert guide[0]["notes"] == "Curated truth"

    summary = create_proposal(
        client, headers, campaign_id, session_id,
        kind="session_summary", title="Session recap", body="The party crossed the threshold.", aliases=[],
    ).json()
    result = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{summary['id']}/approve",
        headers=headers,
    ).json()
    assert result["status"] == "approved"
    assert result["promoted_guide_entry_id"] is None
    assert len(client.get(f"/api/v1/campaigns/{campaign_id}/guide", headers=headers).json()) == 1


def test_proposal_can_be_rejected_and_filtered(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)
    proposal = create_proposal(client, headers, campaign_id, session_id, kind="unresolved_question").json()
    rejected = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{proposal['id']}/reject",
        headers=headers,
    )
    assert rejected.json()["status"] == "rejected"
    filtered = client.get(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals?status=rejected",
        headers=headers,
    )
    assert [item["id"] for item in filtered.json()] == [proposal["id"]]
    campaign_inbox = client.get(
        f"/api/v1/campaigns/{campaign_id}/analysis-proposals?status=rejected",
        headers=headers,
    )
    assert [item["id"] for item in campaign_inbox.json()] == [proposal["id"]]
    assert client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{proposal['id']}/approve",
        headers=headers,
    ).status_code == 409

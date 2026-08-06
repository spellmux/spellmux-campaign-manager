import subprocess
import uuid

import pytest
from test_auth_campaigns import configured_client, create_campaign_and_session, login

from campaign_manager.publishing import publish_to_otterwiki, validate_target_path


def test_player_visible_approved_findings_generate_versioned_draft(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    campaign_id, session_id = create_campaign_and_session(client, headers)
    proposal = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals",
        headers=headers,
        json={
            "kind": "session_summary", "title": "Recap",
            "body": "The party entered Wonderland.", "visibility": "player",
        },
    ).json()
    client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{proposal['id']}/approve",
        headers=headers,
    )

    first = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/publications",
        headers=headers, json={},
    )
    second = client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/publications",
        headers=headers, json={"target_path": "session summaries/session 01.md"},
    )
    assert first.status_code == 201
    assert first.json()["revision"] == 1
    assert "The party entered Wonderland." in first.json()["content"]
    assert second.json()["revision"] == 2
    assert client.post(
        f"/api/v1/campaigns/{campaign_id}/sessions/{session_id}/publications/{second.json()['id']}/publish",
        headers=headers, json={"confirm_overwrite": False},
    ).status_code == 409


def test_otterwiki_adapter_commits_and_protects_existing_pages(tmp_path) -> None:
    repository = tmp_path / "wiki"
    repository.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repository)], check=True)
    (repository / "home.md").write_text("# Home\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "home.md"], check=True)
    subprocess.run([
        "git", "-C", str(repository), "-c", "user.name=Test", "-c",
        "user.email=test@example.test", "commit", "-m", "Initial",
    ], check=True)

    commit, content_hash = publish_to_otterwiki(
        repository, "session summaries/session 01.md", "# Session 1\n", "Publish session",
        None, False,
    )
    assert len(commit) == 40
    assert len(content_hash) == 64
    assert (repository / "session summaries" / "session 01.md").read_text() == "# Session 1\n"
    with pytest.raises(FileExistsError):
        publish_to_otterwiki(
            repository, "session summaries/session 01.md", "changed", "Replace", None, False,
        )


@pytest.mark.parametrize("path", ["../outside.md", "/absolute.md", ".git/config.md", "page.txt"])
def test_publish_path_rejects_unsafe_targets(path) -> None:
    with pytest.raises(ValueError):
        validate_target_path(path)


def test_every_analysis_kind_is_either_published_or_deliberately_withheld() -> None:
    # The renderer's section map silently outlived a kind rename once: approved npc
    # findings stopped appearing on player pages with nothing to show it happened.
    from campaign_manager.models import AnalysisProposal, GameSession
    from campaign_manager.publishing import render_player_draft
    from campaign_manager.schemas import ANALYSIS_KINDS

    # Handled outside the section map, or GM-only table logistics.
    withheld = {"session_summary", "follow_up", "table_note", "pronunciation", "instruction"}
    game_session = GameSession(
        id=uuid.uuid4(), title="Coverage", description="", campaign_id=uuid.uuid4(),
        created_by_id=uuid.uuid4(),
    )
    unpublished = []
    for kind in sorted(ANALYSIS_KINDS - withheld):
        draft = render_player_draft(game_session, [AnalysisProposal(
            session_id=game_session.id, kind=kind, title=f"{kind}-title",
            body=f"{kind}-body", aliases=[], evidence=[], visibility="player",
            status="approved", created_by_id=game_session.created_by_id,
        )])
        if f"{kind}-title" not in draft:
            unpublished.append(kind)
    assert unpublished == []

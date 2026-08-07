import uuid

from sqlalchemy import select
from test_auth_campaigns import configured_client, login

from campaign_manager.compute import select_analysis_target
from campaign_manager.database import session_factory
from campaign_manager.models import ComputeWorker, User


def _payload(**overrides):
    return {
        "name": "Sharn",
        "provider": "ollama",
        "base_url": "http://192.168.99.232:11434",
        "capabilities": ["analysis"],
        "analysis_model": "qwen3.5:4b-q8_0",
        "priority": 50,
        "concurrency": 1,
        "enabled": True,
        **overrides,
    }


def test_instance_admin_manages_and_tests_compute_worker(tmp_path, monkeypatch) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    created = client.post("/api/v1/compute-workers", headers=headers, json=_payload())

    assert created.status_code == 201
    assert created.json()["last_status"] == "unknown"
    worker_id = created.json()["id"]
    monkeypatch.setattr(
        "campaign_manager.api.probe_ollama",
        lambda base_url, model, timeout=3: {
            "ready": True, "model": model, "models": [model], "detail": None,
        },
    )

    tested = client.post(f"/api/v1/compute-workers/{worker_id}/test", headers=headers)
    listed = client.get("/api/v1/compute-workers", headers=headers)

    assert tested.status_code == 200
    assert tested.json()["ready"] is True
    assert tested.json()["worker"]["last_status"] == "ready"
    assert listed.json()[0]["available_models"] == ["qwen3.5:4b-q8_0"]


def test_compute_worker_rejects_credentialed_or_non_http_url(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}

    assert client.post(
        "/api/v1/compute-workers", headers=headers,
        json=_payload(base_url="file:///etc/passwd"),
    ).status_code == 422
    assert client.post(
        "/api/v1/compute-workers", headers=headers,
        json=_payload(base_url="http://user:secret@worker:11434"),
    ).status_code == 422


def test_non_admin_cannot_list_compute_workers(tmp_path) -> None:
    client = configured_client(tmp_path)
    token = login(client)
    with session_factory()() as database:
        user = database.scalar(select(User))
        user.is_instance_admin = False
        database.commit()

    response = client.get(
        "/api/v1/compute-workers", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403


def test_analysis_routing_prefers_highest_priority_healthy_worker(
    tmp_path, monkeypatch
) -> None:
    client = configured_client(tmp_path)
    del client
    with session_factory()() as database:
        user = database.scalar(select(User))
        database.add_all([
            ComputeWorker(
                name="Unavailable", provider="ollama", base_url="http://bad:11434",
                capabilities=["analysis"], analysis_model="qwen3.5:9b", priority=100,
                concurrency=1, enabled=True, created_by_id=user.id,
            ),
            ComputeWorker(
                name="Sharn", provider="ollama", base_url="http://sharn:11434",
                capabilities=["analysis"], analysis_model="qwen3.5:4b-q8_0", priority=50,
                concurrency=1, enabled=True, created_by_id=user.id,
            ),
        ])
        database.commit()
        monkeypatch.setattr(
            "campaign_manager.compute.probe_ollama",
            lambda base_url, model, timeout=3: {
                "ready": base_url == "http://sharn:11434",
                "model": model, "models": [model] if "sharn" in base_url else [],
                "detail": None if "sharn" in base_url else "offline",
            },
        )
        from test_analysis import _settings

        routed, target, status = select_analysis_target(database, _settings(tmp_path))

    assert status["ready"] is True
    assert target.name == "Sharn"
    assert target.worker_id is not None
    assert routed.analysis_base_url == "http://sharn:11434"
    assert routed.analysis_model == "qwen3.5:4b-q8_0"


def test_compute_worker_can_be_updated_disabled_and_deleted(tmp_path) -> None:
    client = configured_client(tmp_path)
    headers = {"Authorization": f"Bearer {login(client)}"}
    worker_id = client.post(
        "/api/v1/compute-workers", headers=headers, json=_payload()
    ).json()["id"]

    updated = client.put(
        f"/api/v1/compute-workers/{worker_id}", headers=headers,
        json=_payload(name="Sharn GPU", enabled=False, priority=75),
    )
    deleted = client.delete(f"/api/v1/compute-workers/{worker_id}", headers=headers)

    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert updated.json()["priority"] == 75
    assert deleted.status_code == 204
    with session_factory()() as database:
        assert database.get(ComputeWorker, uuid.UUID(worker_id)) is None

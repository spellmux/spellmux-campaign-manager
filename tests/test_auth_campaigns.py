from fastapi.testclient import TestClient

from campaign_manager.api import create_app
from campaign_manager.auth import hash_password
from campaign_manager.database import configure_database, session_factory
from campaign_manager.models import Base, User


def configured_client(tmp_path) -> TestClient:
    engine = configure_database(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with session_factory()() as database:
        database.add(
            User(
                email="gm@example.test",
                display_name="Game Master",
                password_hash=hash_password("correct horse battery staple"),
                is_instance_admin=True,
            )
        )
        database.commit()
    return TestClient(create_app())


def login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "GM@Example.Test", "password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    return response.json()["access_token"]


def test_authentication_and_current_user(tmp_path) -> None:
    client = configured_client(tmp_path)
    token = login(client)

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "gm@example.test"
    assert response.json()["is_instance_admin"] is True


def test_invalid_login_does_not_issue_token(tmp_path) -> None:
    client = configured_client(tmp_path)

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "gm@example.test", "password": "incorrect password"},
    )

    assert response.status_code == 401
    assert "access_token" not in response.text


def test_campaign_owner_can_create_and_list_campaign(tmp_path) -> None:
    client = configured_client(tmp_path)
    token = login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = client.post(
        "/api/v1/campaigns",
        headers=headers,
        json={"name": "The Wild Beyond the Witchlight", "description": "A test campaign"},
    )
    listed = client.get("/api/v1/campaigns", headers=headers)

    assert created.status_code == 201
    assert created.json()["slug"] == "the-wild-beyond-the-witchlight"
    assert created.json()["role"] == "owner"
    assert listed.status_code == 200
    assert listed.json() == [created.json()]


def test_campaign_endpoints_require_authentication(tmp_path) -> None:
    client = configured_client(tmp_path)

    assert client.get("/api/v1/campaigns").status_code == 401
    assert client.post("/api/v1/campaigns", json={"name": "Nope"}).status_code == 401


def test_readiness_checks_database(tmp_path) -> None:
    client = configured_client(tmp_path)

    response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


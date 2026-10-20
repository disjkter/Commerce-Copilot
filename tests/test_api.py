from fastapi.testclient import TestClient
from uuid import uuid4

from commerce_copilot.api import app


client = TestClient(app)


def demo_headers() -> dict[str, str]:
    response = client.post(
        "/v1/auth/login",
        json={"username": "demo", "password": "demo12345"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_frontend_is_served_with_assets():
    page = client.get("/")
    stylesheet = client.get("/assets/styles.css")
    script = client.get("/assets/app.js")

    assert page.status_code == 200
    assert "Commerce Copilot" in page.text
    assert stylesheet.status_code == 200
    assert script.status_code == 200
    assert "renderTrace" in script.text


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_turn_endpoint_exposes_agent_trace():
    conversation_id = f"api-{uuid4().hex}"
    response = client.post(
        "/v1/turns",
        headers=demo_headers(),
        json={
            "message": "我的订单可以退货吗？",
            "conversation_id": conversation_id,
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["conversation_id"] == conversation_id
    assert body["reports"][0]["role"] == "order_care"
    assert body["elapsed_ms"] >= 0
    assert body["trace"]["planning_source"] == "local"
    assert body["trace"]["answer_source"] == "local"
    assert body["trace"]["selected_agents"] == ["order_care"]
    assert body["trace"]["grounding"]["passed"] is True


def test_turn_endpoint_generates_conversation_id_when_missing():
    response = client.post(
        "/v1/turns",
        headers=demo_headers(),
        json={"message": "你好"},
    )

    assert response.status_code == 200
    assert len(response.json()["conversation_id"]) == 32


def test_turn_endpoint_requires_authentication():
    response = client.post("/v1/turns", json={"message": "你好"})

    assert response.status_code == 401


def test_register_and_login_issue_access_tokens():
    username = f"user_{uuid4().hex[:10]}"
    registration = client.post(
        "/v1/auth/register",
        json={"username": username, "password": "safe-password-123"},
    )
    login = client.post(
        "/v1/auth/login",
        json={"username": username, "password": "safe-password-123"},
    )

    assert registration.status_code == 201
    assert registration.json()["user"]["username"] == username
    assert login.status_code == 200
    assert login.json()["access_token"]


def test_conversation_cannot_be_reused_by_another_user():
    first_name = f"first_{uuid4().hex[:8]}"
    second_name = f"second_{uuid4().hex[:8]}"
    first = client.post(
        "/v1/auth/register",
        json={"username": first_name, "password": "safe-password-123"},
    ).json()
    second = client.post(
        "/v1/auth/register",
        json={"username": second_name, "password": "safe-password-123"},
    ).json()
    conversation_id = f"owned-{uuid4().hex}"

    owner_response = client.post(
        "/v1/turns",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        json={"message": "你好", "conversation_id": conversation_id},
    )
    intruder_response = client.post(
        "/v1/turns",
        headers={"Authorization": f"Bearer {second['access_token']}"},
        json={"message": "你好", "conversation_id": conversation_id},
    )

    assert owner_response.status_code == 200
    assert intruder_response.status_code == 403

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
    assert 'id="preferences-dialog"' in page.text
    assert 'id="preferences-button"' in page.text
    assert "savePreferences" in script.text


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


def test_authenticated_user_can_save_preferences():
    username = f"prefs_{uuid4().hex[:10]}"
    registration = client.post(
        "/v1/auth/register",
        json={"username": username, "password": "safe-password-123"},
    ).json()
    headers = {"Authorization": f"Bearer {registration['access_token']}"}
    preferences = {
        "max_budget_cents": 50000,
        "preferred_categories": ["耳机", "耳机", "  "],
        "preferred_brands": [],
        "scenarios": ["通勤"],
        "priorities": ["降噪"],
    }

    saved = client.put("/v1/me/preferences", headers=headers, json=preferences)
    loaded = client.get("/v1/me/preferences", headers=headers)

    assert saved.status_code == 200
    assert saved.json()["preferred_categories"] == ["耳机"]
    assert loaded.json() == saved.json()

    turn = client.post(
        "/v1/turns",
        headers=headers,
        json={"message": "推荐商品"},
    )
    assert turn.status_code == 200
    assert turn.json()["trace"]["applied_preferences"] == {
        "max_budget_cents": 50000,
        "preferred_categories": ["耳机"],
        "scenarios": ["通勤"],
        "priorities": ["降噪"],
    }

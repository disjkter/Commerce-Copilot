from fastapi.testclient import TestClient
from uuid import uuid4

from commerce_copilot.api import app


client = TestClient(app)


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
    response = client.post("/v1/turns", json={"message": "你好"})

    assert response.status_code == 200
    assert len(response.json()["conversation_id"]) == 32

from fastapi.testclient import TestClient

from commerce_copilot.api import app


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_turn_endpoint_exposes_agent_trace():
    response = client.post("/v1/turns", json={"message": "我的订单可以退货吗？"})
    body = response.json()

    assert response.status_code == 200
    assert body["reports"][0]["role"] == "order_care"
    assert body["elapsed_ms"] >= 0
    assert body["trace"]["planning_source"] == "local"
    assert body["trace"]["selected_agents"] == ["order_care"]
    assert body["trace"]["grounding"]["passed"] is True

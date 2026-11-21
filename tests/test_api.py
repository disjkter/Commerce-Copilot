from fastapi.testclient import TestClient
import json
from uuid import uuid4

from commerce_copilot.api import app
from commerce_copilot.config import get_settings
from commerce_copilot.logistics import create_webhook_signature


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
    assert page.headers["cache-control"] == "no-store, max-age=0"
    assert "Commerce Copilot" in page.text
    assert stylesheet.status_code == 200
    assert script.status_code == 200
    assert "renderTrace" in script.text
    assert 'id="preferences-dialog"' in page.text
    assert 'id="preferences-button"' in page.text
    assert "savePreferences" in script.text
    assert "/assets/app.js?v=20261120-2" in page.text
    assert 'id="trace-id"' in page.text
    assert 'id="memory-section"' in page.text
    assert "renderMemory" in script.text
    assert "/v1/turns/stream" in script.text
    assert "createStreamingAnswerRenderer" in script.text
    assert 'parsed.event === "answer_delta"' in script.text


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_prometheus_metrics_endpoint():
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE commerce_turns_total counter" in response.text
    assert "# TYPE commerce_turn_duration_seconds histogram" in response.text


def test_logistics_webhook_rejects_an_invalid_signature():
    response = client.post(
        "/v1/webhooks/logistics/SF",
        content=b"{}",
        headers={
            "Content-Type": "application/json",
            "X-Logistics-Signature": "sha256=invalid",
        },
    )

    assert response.status_code == 401


def test_logistics_webhook_is_authenticated_and_idempotent():
    event_id = f"unknown-{uuid4().hex}"
    body = json.dumps(
        {
            "provider_event_id": event_id,
            "tracking_number": "SF999999999999",
            "status": "in_transit",
            "location": "测试转运中心",
            "description": "测试物流事件",
            "occurred_at": "2026-11-15T10:00:00+08:00",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Logistics-Signature": create_webhook_signature(
            body,
            get_settings().logistics_webhook_secret,
        ),
    }

    first = client.post("/v1/webhooks/logistics/SF", content=body, headers=headers)
    duplicate = client.post(
        "/v1/webhooks/logistics/SF",
        content=body,
        headers=headers,
    )

    assert first.status_code == 200
    assert first.json()["result"] == "shipment_not_found"
    assert duplicate.status_code == 200
    assert duplicate.json()["result"] == "duplicate"


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
    assert body["reports"][0]["role"] == "after_sales_advisor"
    assert body["elapsed_ms"] >= 0
    assert body["trace"]["planning_source"] == "local"
    assert body["trace"]["answer_source"] == "local"
    assert body["trace"]["selected_agents"] == ["after_sales_advisor"]
    assert body["trace"]["query_understanding"]["intents"] == ["after_sales"]
    assert body["trace"]["grounding"]["passed"] is True
    assert len(body["trace"]["trace_id"]) == 32
    assert {item["stage"] for item in body["trace"]["stage_timings"]} >= {
        "repository_setup",
        "planning",
        "agent",
        "grounding",
        "answering",
        "persistence",
        "memory_item_persistence",
    }


def test_user_can_inspect_and_forget_an_atomic_memory():
    headers = demo_headers()
    conversation_id = f"memory-api-{uuid4().hex}"
    turn = client.post(
        "/v1/turns",
        headers=headers,
        json={
            "message": "我不喜欢入耳式耳机",
            "conversation_id": conversation_id,
        },
    )
    assert turn.status_code == 200

    listed = client.get("/v1/memories", headers=headers)
    assert listed.status_code == 200
    memory = next(
        item
        for item in listed.json()
        if item["key"] == "preference.feature.入耳式"
        and item["status"] == "active"
    )
    assert memory["value"] == {"term": "入耳式", "accepted": False}
    assert memory["source_type"] == "user_explicit"

    forgotten = client.delete(
        f"/v1/memories/{memory['memory_id']}",
        headers=headers,
    )
    assert forgotten.status_code == 204
    audit = client.get(
        "/v1/memories?include_invalidated=true",
        headers=headers,
    )
    revoked = next(
        item for item in audit.json() if item["memory_id"] == memory["memory_id"]
    )
    assert revoked["status"] == "invalidated"
    assert revoked["valid_to"] is not None


def test_stream_endpoint_emits_progress_and_complete_response():
    response = client.post(
        "/v1/turns/stream",
        headers=demo_headers(),
        json={"message": "推荐一个键盘，同时告诉我发货规则"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        event_name = lines[0].removeprefix("event: ")
        payload = json.loads(lines[1].removeprefix("data: "))
        events.append((event_name, payload))

    event_names = [name for name, _ in events]
    assert event_names[0] == "planning_started"
    assert "planning_completed" in event_names
    assert event_names.count("agent_started") == 2
    assert event_names.count("agent_completed") == 2
    assert "grounding_completed" in event_names
    assert "answer_completed" in event_names
    assert "answer_started" in event_names
    assert "answer_delta" in event_names
    assert "answer_finished" in event_names
    assert "workflow_completed" in event_names
    assert event_names[-1] == "complete"
    completed = events[-1][1]["response"]
    streamed_answer = "".join(
        payload["delta"] for name, payload in events if name == "answer_delta"
    )
    assert streamed_answer == completed["answer"]
    assert event_names.index("answer_started") < event_names.index("answer_finished")
    assert event_names.index("answer_finished") < event_names.index("complete")
    assert completed["trace"]["grounding"]["passed"] is True
    assert len(completed["trace"]["trace_id"]) == 32


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

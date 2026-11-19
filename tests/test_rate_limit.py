import hashlib
from dataclasses import replace

from fastapi.testclient import TestClient
from starlette.requests import Request

from commerce_copilot.api import _client_identity, app
from commerce_copilot.config import get_settings
from commerce_copilot.rate_limit import InMemorySlidingWindowRateLimiter


def test_sliding_window_rejects_and_recovers_after_window():
    limiter = InMemorySlidingWindowRateLimiter()

    first = limiter.check("turn:user-a", limit=2, window_seconds=10, now=100)
    second = limiter.check("turn:user-a", limit=2, window_seconds=10, now=101)
    rejected = limiter.check("turn:user-a", limit=2, window_seconds=10, now=102)
    recovered = limiter.check("turn:user-a", limit=2, window_seconds=10, now=111)

    assert first.allowed is True
    assert first.remaining == 1
    assert second.allowed is True
    assert rejected.allowed is False
    assert rejected.retry_after_seconds == 8
    assert recovered.allowed is True


def test_rate_limits_are_isolated_by_identity():
    limiter = InMemorySlidingWindowRateLimiter()

    assert limiter.check("turn:user-a", limit=1, window_seconds=60, now=1).allowed
    assert not limiter.check("turn:user-a", limit=1, window_seconds=60, now=2).allowed
    assert limiter.check("turn:user-b", limit=1, window_seconds=60, now=2).allowed


def test_login_endpoint_returns_429_and_retry_after():
    client = TestClient(app)
    limiter = app.state.rate_limiter
    settings = get_settings()
    limiter.reset()
    identity_hash = hashlib.sha256("testclient".encode("utf-8")).hexdigest()[:24]
    key = f"login:{identity_hash}"
    try:
        for _ in range(settings.login_rate_limit):
            assert limiter.check(
                key,
                limit=settings.login_rate_limit,
                window_seconds=settings.login_rate_window_seconds,
            ).allowed

        response = client.post(
            "/v1/auth/login",
            json={"username": "demo", "password": "wrong-password"},
        )

        assert response.status_code == 429
        assert int(response.headers["retry-after"]) >= 1
        assert response.headers["x-ratelimit-remaining"] == "0"
    finally:
        limiter.reset()


def test_forwarded_ip_is_only_used_when_proxy_headers_are_trusted():
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"203.0.113.8, 10.0.0.2")],
            "client": ("10.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
            "http_version": "1.1",
        }
    )
    settings = get_settings()

    assert _client_identity(request, replace(settings, trust_proxy_headers=False)) == "10.0.0.1"
    assert _client_identity(request, replace(settings, trust_proxy_headers=True)) == "203.0.113.8"

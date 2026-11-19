from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_container_runs_application_as_non_root_user():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim-bookworm" in dockerfile
    assert "USER commerce" in dockerfile
    assert 'CMD ["commerce-copilot"]' in dockerfile


def test_compose_keeps_database_private_and_api_on_loopback():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "pgvector/pgvector:0.8.7-pg14-bookworm" in compose
    assert '"127.0.0.1:8010:8010"' in compose
    assert "5432:5432" not in compose
    assert 'command: ["alembic", "upgrade", "head"]' in compose
    assert "no-new-privileges:true" in compose


def test_production_secrets_are_templates_not_committed_values():
    example = (ROOT / ".env.production.example").read_text(encoding="utf-8")

    assert "POSTGRES_PASSWORD=replace-with" in example
    assert "LLM_API_KEY=replace-with" in example
    assert "AUTH_SECRET=replace-with" in example


def test_nginx_proxy_supports_sse_without_buffering():
    nginx = (
        ROOT / "ops" / "nginx" / "commerce-copilot.conf"
    ).read_text(encoding="utf-8")

    assert "proxy_pass http://127.0.0.1:8010" in nginx
    assert "proxy_buffering off" in nginx
    assert "X-Forwarded-For" in nginx

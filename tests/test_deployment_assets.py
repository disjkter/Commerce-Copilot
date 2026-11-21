from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_container_runs_application_as_non_root_user():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim-bookworm" in dockerfile
    assert "https://mirrors.aliyun.com/pypi/simple/" in dockerfile
    assert "USER commerce" in dockerfile
    assert 'CMD ["commerce-copilot"]' in dockerfile


def test_compose_keeps_stateful_services_private_and_api_on_loopback():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "pgvector/pgvector:0.8.7-pg14-bookworm" in compose
    assert "redis:7.4-alpine" in compose
    assert '"127.0.0.1:8010:8010"' in compose
    assert "5432:5432" not in compose
    assert "6379:6379" not in compose
    assert "init-vector.sql:/docker-entrypoint-initdb.d/10-vector.sql:ro" in compose
    assert 'command: ["alembic", "upgrade", "head"]' in compose
    assert "no-new-privileges:true" in compose


def test_compose_runs_redis_backed_worker_with_persistent_stream_data():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "worker:" in compose
    assert 'command: ["commerce-copilot-worker"]' in compose
    assert "TASK_QUEUE_BACKEND: redis" in compose
    assert "REDIS_URL: redis://redis:6379/0" in compose
    assert "redis_data:/data" in compose
    assert "redis_data:" in compose


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


def test_postgres_initialization_enables_vector_extension():
    init_sql = (
        ROOT / "ops" / "postgres" / "init-vector.sql"
    ).read_text(encoding="utf-8")

    assert init_sql.strip() == "CREATE EXTENSION IF NOT EXISTS vector;"

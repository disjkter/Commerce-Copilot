import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str
    app_host: str
    app_port: int
    database_path: Path
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_provider: str = "openai_compatible"
    planner_model: str = ""
    composer_model: str = ""
    llm_structured_output: bool = True
    auth_secret: str = "local-development-secret-change-before-deploying"
    access_token_minutes: int = 120
    database_url: str = ""
    log_level: str = "INFO"
    embedding_provider: str = "feature_hash"
    embedding_model: str = "BAAI/bge-m3"
    embedding_model_path: str = ""
    embedding_device: str = "cpu"
    embedding_warmup_enabled: bool = True
    retrieval_strategy: str = "adaptive"
    logistics_webhook_secret: str = (
        "local-logistics-webhook-secret-change-before-deploying"
    )
    rate_limit_enabled: bool = True
    trust_proxy_headers: bool = False
    login_rate_limit: int = 20
    login_rate_window_seconds: int = 60
    register_rate_limit: int = 10
    register_rate_window_seconds: int = 300
    turn_rate_limit: int = 60
    turn_rate_window_seconds: int = 60
    webhook_rate_limit: int = 300
    webhook_rate_window_seconds: int = 60
    context_compaction_enabled: bool = True
    context_recent_turns: int = 4
    task_queue_backend: str = "in_memory"
    redis_url: str = "redis://127.0.0.1:6379/0"
    task_stream_key: str = "commerce:turn_tasks"
    task_consumer_group: str = "commerce-workers"
    task_lease_seconds: int = 120


@lru_cache
def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "Commerce Copilot"),
        app_host=os.getenv("APP_HOST", "127.0.0.1"),
        app_port=int(os.getenv("APP_PORT", "8010")),
        database_path=Path(os.getenv("DATABASE_PATH", "runtime/commerce.db")),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
        llm_model=os.getenv("LLM_MODEL", "deepseek-flash"),
        llm_provider=os.getenv("LLM_PROVIDER", "openai_compatible"),
        planner_model=os.getenv("PLANNER_MODEL", ""),
        composer_model=os.getenv("COMPOSER_MODEL", ""),
        llm_structured_output=os.getenv("LLM_STRUCTURED_OUTPUT", "true").casefold()
        in {"1", "true", "yes", "on"},
        auth_secret=os.getenv(
            "AUTH_SECRET",
            "local-development-secret-change-before-deploying",
        ),
        access_token_minutes=int(os.getenv("ACCESS_TOKEN_MINUTES", "120")),
        database_url=os.getenv("DATABASE_URL", ""),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        embedding_provider=os.getenv("EMBEDDING_PROVIDER", "feature_hash"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
        embedding_model_path=os.getenv("EMBEDDING_MODEL_PATH", ""),
        embedding_device=os.getenv("EMBEDDING_DEVICE", "cpu"),
        embedding_warmup_enabled=os.getenv(
            "EMBEDDING_WARMUP_ENABLED", "true"
        ).casefold()
        in {"1", "true", "yes", "on"},
        retrieval_strategy=os.getenv("RETRIEVAL_STRATEGY", "adaptive").casefold(),
        logistics_webhook_secret=os.getenv(
            "LOGISTICS_WEBHOOK_SECRET",
            "local-logistics-webhook-secret-change-before-deploying",
        ),
        rate_limit_enabled=os.getenv("RATE_LIMIT_ENABLED", "true").casefold()
        in {"1", "true", "yes", "on"},
        trust_proxy_headers=os.getenv("TRUST_PROXY_HEADERS", "false").casefold()
        in {"1", "true", "yes", "on"},
        login_rate_limit=int(os.getenv("LOGIN_RATE_LIMIT", "20")),
        login_rate_window_seconds=int(
            os.getenv("LOGIN_RATE_WINDOW_SECONDS", "60")
        ),
        register_rate_limit=int(os.getenv("REGISTER_RATE_LIMIT", "10")),
        register_rate_window_seconds=int(
            os.getenv("REGISTER_RATE_WINDOW_SECONDS", "300")
        ),
        turn_rate_limit=int(os.getenv("TURN_RATE_LIMIT", "60")),
        turn_rate_window_seconds=int(os.getenv("TURN_RATE_WINDOW_SECONDS", "60")),
        webhook_rate_limit=int(os.getenv("WEBHOOK_RATE_LIMIT", "300")),
        webhook_rate_window_seconds=int(
            os.getenv("WEBHOOK_RATE_WINDOW_SECONDS", "60")
        ),
        context_compaction_enabled=os.getenv(
            "CONTEXT_COMPACTION_ENABLED", "true"
        ).casefold()
        in {"1", "true", "yes", "on"},
        context_recent_turns=max(1, min(20, int(os.getenv("CONTEXT_RECENT_TURNS", "4")))),
        task_queue_backend=os.getenv("TASK_QUEUE_BACKEND", "in_memory").casefold(),
        redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
        task_stream_key=os.getenv("TASK_STREAM_KEY", "commerce:turn_tasks"),
        task_consumer_group=os.getenv(
            "TASK_CONSUMER_GROUP", "commerce-workers"
        ),
        task_lease_seconds=max(
            30,
            min(900, int(os.getenv("TASK_LEASE_SECONDS", "120"))),
        ),
    )


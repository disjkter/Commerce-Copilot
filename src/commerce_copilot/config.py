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
    auth_secret: str = "local-development-secret-change-before-deploying"
    access_token_minutes: int = 120
    database_url: str = ""
    log_level: str = "INFO"
    embedding_provider: str = "feature_hash"
    embedding_model: str = "BAAI/bge-m3"
    embedding_model_path: str = ""
    embedding_device: str = "cpu"
    retrieval_strategy: str = "adaptive"


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
        retrieval_strategy=os.getenv("RETRIEVAL_STRATEGY", "adaptive").casefold(),
    )


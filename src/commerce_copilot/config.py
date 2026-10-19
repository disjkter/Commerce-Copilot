import os
from dataclasses import dataclass
from functools import lru_cache

@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str
    app_host: str
    app_port: int
    llm_api_key: str
    llm_base_url: str
    llm_model: str


@lru_cache
def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "Commerce Copilot"),
        app_host=os.getenv("APP_HOST", "127.0.0.1"),
        app_port=int(os.getenv("APP_PORT", "8010")),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
        llm_model=os.getenv("LLM_MODEL", "deepseek-chat"),
    )


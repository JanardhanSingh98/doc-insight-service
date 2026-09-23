from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DIS_", extra="ignore")

    app_name: str = "doc-insight-service"
    environment: str = "local"
    log_level: str = "INFO"

    # Fake upstream "LLM gateway". Points at our own /mock endpoints by default
    # so the service runs with zero external dependencies.
    llm_base_url: str = "http://127.0.0.1:8000/mock"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 5.0

    embedding_dim: int = 64
    chunk_size: int = 400
    chunk_overlap: int = 40

    max_concurrent_embeddings: int = 4
    worker_count: int = 2
    job_max_retries: int = 3

    cache_ttl_seconds: int = 60
    upload_dir: str = "data/uploads"


@lru_cache
def get_settings() -> Settings:
    return Settings()

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    upstream_llama_url: str = "http://llamacpp:8080"
    upstream_vllm_url: str = "http://vllm:8000"

    default_backend: str = "llamacpp"

    secret_key: str
    api_key_hmac_pepper: str = ""

    request_id_header: str = "X-Request-ID"
    log_level: str = "INFO"

    gateway_persist_logs: bool = True

    inference_max_concurrency: int = 4
    inference_queue_size: int = 10
    inference_queue_timeout_s: float = 30.0

    batch_window_ms: float = 50.0
    batch_max_size: int = 8

    # HTTP client (httpx) connection settings
    http_connect_timeout_s: float = 30.0
    http_read_timeout_s: float = 600.0
    http_write_timeout_s: float = 60.0
    http_pool_timeout_s: float = 30.0
    http_max_connections: int = 256
    http_max_keepalive_connections: int = 128

    def dsn_asyncpg(self) -> str:
        u = self.database_url
        if u.startswith("postgres://"):
            return "postgresql://" + u[len("postgres://") :]
        return u

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    upstream_llama_url: str = "http://llamacpp:8080"

    secret_key: str
    api_key_hmac_pepper: str = ""

    request_id_header: str = "X-Request-ID"
    log_level: str = "INFO"

    gateway_persist_logs: bool = True

    def dsn_asyncpg(self) -> str:
        u = self.database_url
        if u.startswith("postgres://"):
            return "postgresql://" + u[len("postgres://") :]
        return u

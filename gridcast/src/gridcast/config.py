"""Runtime configuration shared by every GridCast process.

Each service reads plain environment variables (12-factor). Kubernetes injects them from
ConfigMaps and Secrets; locally they come from `.env`. Service-specific settings live next to
the service and subclass `BaseServiceSettings`.
"""

from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Connection settings for the estate's PostgreSQL database."""

    model_config = SettingsConfigDict(env_prefix="GRIDCAST_DB_", env_file=".env", extra="ignore")

    host: str = "localhost"
    port: int = 5432
    name: str = "gridcast"
    user: str = "gridcast_app"
    password: SecretStr = SecretStr("gridcast_app")
    pool_size: int = 5
    max_overflow: int = 5
    pool_timeout_seconds: float = 10.0
    # Connections are recycled periodically, like most production pools. This is what makes a
    # credential rotation surface minutes later rather than immediately.
    pool_recycle_seconds: int = 300
    connect_timeout_seconds: int = 5
    statement_timeout_ms: int = 60_000
    application_name: str | None = None

    def sqlalchemy_url(self) -> str:
        password = quote_plus(self.password.get_secret_value())
        return (
            f"postgresql+psycopg://{quote_plus(self.user)}:{password}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class BaseServiceSettings(BaseSettings):
    """Settings common to every service process."""

    model_config = SettingsConfigDict(env_prefix="GRIDCAST_", env_file=".env", extra="ignore")

    environment: str = "local"
    log_level: str = "INFO"
    log_format: str = Field(default="json", pattern="^(json|text)$")
    http_host: str = "0.0.0.0"
    http_port: int = 8080
    release_file: str = "/app/release.json"


@lru_cache
def database_settings() -> DatabaseSettings:
    return DatabaseSettings()

"""Application configuration using Pydantic BaseSettings."""
# ruff: noqa: I001 - Imports structured for Jinja2 template conditionals

from pathlib import Path
from typing import Literal

from pydantic import computed_field, field_validator, ValidationInfo
from pydantic_settings import BaseSettings, SettingsConfigDict


def find_env_file() -> Path | None:
    """Find .env file in current or parent directories."""
    current = Path.cwd()
    for path in [current, current.parent]:
        env_file = path / ".env"
        if env_file.exists():
            return env_file
    return None


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=find_env_file(),
        env_ignore_empty=True,
        extra="ignore",
    )

    # === Project ===
    PROJECT_NAME: str = "agents_bots"
    API_V1_STR: str = "/api/v1"
    PORT: int = 8888
    DEBUG: bool = False
    ENVIRONMENT: Literal["development", "local", "staging", "production"] = "local"

    # === Logfire ===
    LOGFIRE_TOKEN: str | None = None
    LOGFIRE_SERVICE_NAME: str = "agents_bots"
    LOGFIRE_ENVIRONMENT: str = "development"

    # === Database (PostgreSQL async) ===
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "agents_bots"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL(self) -> str:
        """Build async PostgreSQL connection URL."""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL_SYNC(self) -> str:
        """Build sync PostgreSQL connection URL (for Alembic)."""
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # Pool configuration
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30

    # === Auth (SECRET_KEY for JWT/Session/Admin) ===
    SECRET_KEY: str = "change-me-in-production-use-openssl-rand-hex-32"

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str, info: ValidationInfo) -> str:
        """Validate SECRET_KEY is secure in production."""
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters long")
        # Get environment from values if available
        env = info.data.get("ENVIRONMENT", "local") if info.data else "local"
        if v == "change-me-in-production-use-openssl-rand-hex-32" and env == "production":
            raise ValueError(
                "SECRET_KEY must be changed in production! "
                "Generate a secure key with: openssl rand -hex 32"
            )
        return v

    # === JWT Settings ===
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30  # 30 minutes
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    ALGORITHM: str = "HS256"

    # === Prometheus ===
    PROMETHEUS_METRICS_PATH: str = "/metrics"
    PROMETHEUS_INCLUDE_IN_SCHEMA: bool = False

    # === AI Agent (langchain, openai / openai-compatible) ===
    # The ChatOpenAI client is OpenAI-API-compatible, so any provider
    # that speaks the same protocol works (OpenAI, OpenRouter,
    # Together, Groq, local llama.cpp, etc.) — just point AI_BASE_URL
    # at it. The OPENAI_API_KEY var holds whichever provider's key.
    OPENAI_API_KEY: str = ""
    AI_MODEL: str = "gpt-4o-mini"
    AI_BASE_URL: str = "https://api.openai.com/v1"
    AI_TEMPERATURE: float = 0.7
    AI_FRAMEWORK: str = "langchain"
    LLM_PROVIDER: str = "openai"

    # === LangSmith (LangChain observability) ===
    LANGCHAIN_TRACING_V2: bool = True
    LANGCHAIN_API_KEY: str | None = None
    LANGCHAIN_PROJECT: str = "agents_bots"
    LANGCHAIN_ENDPOINT: str = "https://api.smith.langchain.com"

    # === OpenPhone (Quo API) ===
    OPENPHONE_API_KEY: str = ""
    OPENPHONE_WEBHOOK_SECRET: str = ""  # key returned when creating a webhook
    OPENPHONE_BASE_URL: str = "https://api.openphone.com/v1"

    # === Lifecycle Pipeline (Alerts + Daily Stats) ===
    # APScheduler is started inside the FastAPI lifespan when
    # ``SCHEDULER_ENABLED=True`` AND we're not in the pytest test
    # environment (``ENVIRONMENT != "test"``). Two jobs run:
    # - ``alert_engine.scan`` every ALERT_ENGINE_INTERVAL_MINUTES (default 5)
    # - ``daily_stats.snapshot`` at STATS_DAILY_HOUR:STATS_DAILY_MINUTE local
    # Thresholds are operator-tunable; alerts only fire if a Job has been
    # stuck in the offending status for longer than the configured
    # minutes.
    SCHEDULER_ENABLED: bool = True
    ALERT_ENGINE_INTERVAL_MINUTES: int = 5
    # A pending job must be dispatched to a tech or rejected by the
    # operator within this window; past it the engine raises an
    # ``undispatched`` alert. Detection latency is bounded by
    # ALERT_ENGINE_INTERVAL_MINUTES (the scan cadence).
    ALERTS_UNDISPATCHED_MINUTES: int = 5
    # After a tech update, the operator must relay it to the source company
    # (natively — we never send). If no operator outbound to the company is
    # observed within this window, a ``company_update_unsent`` reminder fires.
    ALERTS_COMPANY_UPDATE_UNSENT_MINUTES: int = 7
    ALERTS_STUCK_DISPATCHED_MINUTES: int = 240  # 4 hours
    ALERTS_STUCK_IN_PROGRESS_MINUTES: int = 480  # 8 hours
    ALERTS_APPT_PASSED_GRACE_MINUTES: int = 60  # 1 hour after the appt
    ALERTS_CLOSING_GRACE_MINUTES: int = 1440  # 24 hours with no close
    STATS_DAILY_HOUR: int = 23
    STATS_DAILY_MINUTE: int = 55

    # === CORS ===
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:8080"]
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: list[str] = ["*"]
    CORS_ALLOW_HEADERS: list[str] = ["*"]

    @field_validator("CORS_ORIGINS")
    @classmethod
    def validate_cors_origins(cls, v: list[str], info: ValidationInfo) -> list[str]:
        """Warn if CORS_ORIGINS is too permissive in production."""
        env = info.data.get("ENVIRONMENT", "local") if info.data else "local"
        if "*" in v and env == "production":
            raise ValueError(
                "CORS_ORIGINS cannot contain '*' in production! Specify explicit allowed origins."
            )
        return v


settings = Settings()

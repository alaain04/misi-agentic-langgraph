from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_NAME: str = "entity-worker"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_PREFIX: str = "/api/v1"
    CORS_ORIGINS: list[str] = ["*"]

    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DB: str = "misi"

    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_DEFAULT_TTL: int = 300

    NATS_URL: str = "nats://localhost:4222"
    NATS_STREAM_NAME: str = "ENTITY_FETCH"
    NATS_SUBJECT_PREFIX: str = "entity.fetch"
    NATS_MAX_DELIVER: int = 5
    NATS_TRANSIENT_BACKOFF_BASE: float = 5.0
    NATS_TRANSIENT_BACKOFF_CAP: float = 300.0

    GITHUB_TOKEN: str = ""
    GITHUB_ISSUES_LOOKBACK_DAYS: int = 30
    GITHUB_RELEASES_LOOKBACK_DAYS: int = 90

    NPM_RATE_WINDOWS: list[tuple[int, int]] = [(60, 500), (3600, 5000)]
    GITHUB_RATE_WINDOWS: list[tuple[int, int]] = [(60, 100), (3600, 5000)]

    WORKER_CONCURRENCY: int = 5
    MAX_RETRIES: int = 3


settings = Settings()

# src/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    nats_url: str = "nats://localhost:4222"
    mongodb_uri: str = "mongodb://localhost:27017/misi"
    redis_url: str = "redis://localhost:6379"

    github_token: str = ""  # required for GitHub fetchers

    github_issues_lookback_days: int = 30
    github_releases_lookback_days: int = 90

    npm_rate_windows: list[tuple[int, int]] = [(60, 500), (3600, 5000)]
    github_rate_windows: list[tuple[int, int]] = [(60, 100), (3600, 5000)]

    worker_concurrency: int = 5
    max_retries: int = 3

    nats_max_deliver: int = 5
    nats_transient_backoff_base: float = 5.0
    nats_transient_backoff_cap: float = 300.0


settings = Settings()

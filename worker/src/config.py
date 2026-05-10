from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    nats_url: str = "nats://localhost:4222"
    mongodb_uri: str = "mongodb://localhost:27017/misi"
    npm_rate_limit_rps: float = 10.0
    github_rate_limit_rps: float = 5.0
    worker_concurrency: int = 5
    max_retries: int = 3


settings = Settings()

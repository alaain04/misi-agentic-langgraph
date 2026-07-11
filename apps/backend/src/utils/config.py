from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # MongoDB
    mongodb_uri: str

    # OpenAI
    openai_api_key: str

    # LangSmith
    langsmith_api_key: str = ""
    langsmith_project: str = "default"

    # GitHub
    github_token: str = ""

    # Docker (runtime subgraph)
    node_docker_image: str = "node:20-slim"
    docker_memory_limit: str = "512m"
    docker_cpu_limit: float = 1.0
    script_timeout_seconds: int = 120

    # Workers service
    workers_url: str = "http://localhost:8001"

    # Tavily (web search for conductor)
    tavily_api_key: str = ""

    # Analysis parameters
    lookback_days: int = 90
    reviewer_batch_size: int = 20
    repo_cache_max_age_days: int = 1
    runtime_cache_max_age_days: int = 30
    # Minimum severity for gate-2 human review. Findings below this threshold are
    # included in the report but do not pause execution for approval.
    # Values: any | low | medium | high | critical  (default: any)
    reviewer_min_severity: str = "any"


settings = Settings()

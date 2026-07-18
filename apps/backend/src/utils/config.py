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

    # CodeGraph (blast-radius analysis of the target repo)
    codegraph_docker_image: str = "codegraph-cli:latest"

    # Tavily (web search for conductor)
    tavily_api_key: str = ""

    # Minimum severity for findings kept in the final report. Findings below this
    # threshold are dropped by save_report_result before persisting.
    # Values: any | low | medium | high | critical  (default: any)
    report_min_severity: str = "any"
    # Minimum severity for vulnerability findings from the dependency audit.
    # npm/pnpm `audit --json` is not filtered by --audit-level, so we filter here.
    # Values: low | medium | high | critical  (default: high)
    vuln_min_severity: str = "high"
    # Cap on concurrent npm registry lookups when resolving licenses for
    # packages whose lockfile doesn't carry a "license" field (yarn/pnpm
    # always; npm when the field is missing) — caps concurrency, not
    # coverage, to avoid hammering the registry on large trees.
    license_lookup_concurrency: int = 10


settings = Settings()

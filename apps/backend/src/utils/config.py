from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # MongoDB
    mongodb_uri: str

    # OpenAI
    openai_api_key: str

    # LangSmith
    langsmith_api_key: str
    langsmith_project: str

    # GitHub
    github_token: str

    # CodeGraph (blast-radius analysis of the target repo)
    codegraph_docker_image: str

    # Trivy (vulnerability/license scanning + SBOM/dependency-graph generation).
    # trivy_cache_dir is a HOST directory, mounted into every trivy container
    # invocation as a persistent cache_volume so the ~100MB vulnerability DB
    # is downloaded once, not on every scan (see docs/superpowers/plans/
    # 2026-07-31-trivy-adoption.md, Prior Art).
    trivy_image: str
    trivy_cache_dir: str

    # Tavily (web search for conductor)
    tavily_api_key: str

    # Minimum severity for risk findings. Findings below this threshold are
    # dropped by save_analysis_result before the report subgraph enriches them
    # (no web_search/blast_radius spent on them), and dropped again by
    # save_report_result as a final guard before persisting.
    # Values: any | low | medium | high | critical
    risk_min_severity: str
    # Minimum severity for vulnerability findings from the dependency audit.
    # npm/pnpm `audit --json` is not filtered by --audit-level, so we filter here.
    # Values: low | medium | high | critical
    vuln_min_severity: str
    # Cap on concurrent npm registry lookups when resolving licenses for
    # packages whose lockfile doesn't carry a "license" field (yarn/pnpm
    # always; npm when the field is missing) — caps concurrency, not
    # coverage, to avoid hammering the registry on large trees.
    license_lookup_concurrency: int


settings = Settings()

"""Repo analysis node."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.subgraphs.ingestion_subgraphs.repo.service import analyze_service
from src.main_graph.subgraphs.ingestion_subgraphs.repo.state import RepoState


async def analyze(state: RepoState, config: RunnableConfig) -> dict:
    from src.main_graph.config import get_services

    svc = get_services(config)
    return await analyze_service(
        state,
        svc["ingestion_daos"]["repo"],
        svc["repo_cache_dao"],
    )

"""Analyze node for the Repo subgraph."""

import asyncio
import random

from src.main_graph.subgraphs.ingestion_subgraphs.repo.state import RepoState


async def analyze(state: RepoState) -> dict:
    await asyncio.sleep(random.uniform(60, 180))
    return {
        "repo_result": {
            "status": "mocked",
            "packages_checked": len(state.get("direct_dependencies", [])),
            "repositories": [],
            "note": "Repo subgraph not yet implemented",
        }
    }

"""Analyze node for the Registry subgraph."""

import asyncio
import random

from src.main_graph.subgraphs.ingestion_subgraphs.registry.state import RegistryState


async def analyze(state: RegistryState) -> dict:
    await asyncio.sleep(random.uniform(60, 180))
    return {
        "registry_result": {
            "status": "mocked",
            "packages_checked": len(state.get("direct_dependencies", [])),
            "outdated": [],
            "vulnerabilities": [],
            "note": "Registry subgraph not yet implemented",
        }
    }

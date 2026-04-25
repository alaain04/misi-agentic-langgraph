"""Mocked registry analysis node."""

import asyncio
import random

from src.graphs.registry.state import RegistryState


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

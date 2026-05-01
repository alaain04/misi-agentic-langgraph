"""Analyze node for the Runtime subgraph."""

import asyncio
import random

from src.main_graph.subgraphs.ingestion_subgraphs.runtime.state import RuntimeState


async def analyze(state: RuntimeState) -> dict:
    await asyncio.sleep(random.uniform(60, 180))
    return {
        "runtime_result": {
            "status": "mocked",
            "packages_checked": len(state.get("direct_dependencies", [])),
            "compatibility_issues": [],
            "note": "Runtime subgraph not yet implemented",
        }
    }

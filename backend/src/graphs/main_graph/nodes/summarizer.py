"""Summarizer node — aggregates subgraph results into a unified summary (mocked)."""

import asyncio
import random

from src.graphs.main_graph.state import MainState


async def summarizer(state: MainState) -> dict:
    await asyncio.sleep(random.uniform(2, 8))
    results = state.get("subgraph_results", [])
    names = [r.get("subgraph", "unknown") for r in results]
    return {
        "summary": (
            f"[Mocked] Summary of {len(results)} subgraph analyses "
            f"({', '.join(names)}). Summarizer not yet implemented."
        )
    }

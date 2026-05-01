import asyncio
import random

from src.main_graph.subgraphs.summarizer.state import SummarizerState


async def summarize(state: SummarizerState) -> dict:
    await asyncio.sleep(random.uniform(2, 8))
    results = state.get("subgraph_results", [])
    names = [r.get("subgraph", "unknown") for r in results]
    return {
        "summary": (
            f"[Mocked] Summary of {len(results)} subgraph analyses "
            f"({', '.join(names)}). Summarizer not yet implemented."
        )
    }

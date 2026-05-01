import asyncio
import random
from typing import Any

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.ingestion_subgraphs._base import AnalysisState


class RegistryState(AnalysisState):
    registry_result: dict[str, Any]


async def _analyze(state: RegistryState) -> dict:
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


def _build() -> StateGraph:
    builder = StateGraph(RegistryState)
    builder.add_node("analyze", _analyze)
    builder.add_edge(START, "analyze")
    builder.add_edge("analyze", END)
    return builder.compile()


registry_subgraph = _build()

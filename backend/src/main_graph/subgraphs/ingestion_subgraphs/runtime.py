import asyncio
import random
from typing import Any

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.ingestion_subgraphs._base import AnalysisState


class RuntimeState(AnalysisState):
    runtime_result: dict[str, Any]


async def _analyze(state: RuntimeState) -> dict:
    await asyncio.sleep(random.uniform(60, 180))
    return {
        "runtime_result": {
            "status": "mocked",
            "packages_checked": len(state.get("direct_dependencies", [])),
            "compatibility_issues": [],
            "note": "Runtime subgraph not yet implemented",
        }
    }


def _build() -> StateGraph:
    builder = StateGraph(RuntimeState)
    builder.add_node("analyze", _analyze)
    builder.add_edge(START, "analyze")
    builder.add_edge("analyze", END)
    return builder.compile()


runtime_subgraph = _build()

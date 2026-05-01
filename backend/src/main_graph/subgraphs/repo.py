import asyncio
import random
from typing import Any

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs._base import AnalysisState


class RepoState(AnalysisState):
    repo_result: dict[str, Any]


async def _analyze(state: RepoState) -> dict:
    await asyncio.sleep(random.uniform(60, 180))
    return {
        "repo_result": {
            "status": "mocked",
            "packages_checked": len(state.get("direct_dependencies", [])),
            "repositories": [],
            "note": "Repo subgraph not yet implemented",
        }
    }


def _build() -> StateGraph:
    builder = StateGraph(RepoState)
    builder.add_node("analyze", _analyze)
    builder.add_edge(START, "analyze")
    builder.add_edge("analyze", END)
    return builder.compile()


repo_subgraph = _build()

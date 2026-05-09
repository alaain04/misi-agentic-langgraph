"""Task dispatcher — conditional edge that fans out the current stage via Send."""

from typing import Any

from langgraph.types import Send

from src.main_graph.constants import EXECUTE_PLAN
from src.main_graph.state import MainState


def task_dispatcher(state: MainState) -> list[Send]:
    """Return one Send per subgraph in the current execution stage."""
    stages = state.get("execution_stages", [])
    idx = state.get("current_stage_index", 0)
    current_stage = stages[idx] if idx < len(stages) else []

    upstream_results: dict[str, Any] = {
        entry["subgraph"]: entry["result_id"]
        for entry in state.get("subgraph_results", [])
        if "result_id" in entry
    }

    return [
        Send(
            EXECUTE_PLAN,
            {
                "subgraph_name": name,
                "job_id": state.get("job_id", ""),
                "direct_dependencies": state.get("direct_dependencies", []),
                "transitive_dependencies": state.get("transitive_dependencies", []),
                "discovery_summary": state.get("discovery_summary", ""),
                "concern": state.get("concern", ""),
                "upstream_results": upstream_results,
                "subgraph_results": [],
            },
        )
        for name in current_stage
    ]

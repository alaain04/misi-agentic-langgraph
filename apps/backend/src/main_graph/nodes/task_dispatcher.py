"""Task dispatcher — fans out the current stage via Send."""

from typing import Any

from langgraph.types import Send

from src.main_graph.constants import EXECUTE_PLAN
from src.main_graph.state import MainState


def task_dispatcher(state: MainState) -> list[Send]:
    """Return one Send per entry in the current execution stage."""
    stages = state.get("execution_stages", [])
    idx = state.get("current_stage_index", 0)
    current_stage: list[dict] = stages[idx] if idx < len(stages) else []

    upstream_results: dict[str, Any] = {
        entry["subgraph"]: entry.get("result_id")
        for entry in state.get("subgraph_results", [])
        if entry.get("result_id")
    }

    return [
        Send(
            EXECUTE_PLAN,
            {
                "subgraph_name": entry["subgraph"],
                "dep_name": entry.get("dep_name"),
                "job_id": state.get("job_id", ""),
                "discovery_summary": state.get("discovery_summary", ""),
                "concern": state.get("concern", ""),
                "sbom_cyclonedx": state.get("sbom_cyclonedx", {}),
                "repo_path": state.get("repo_path"),
                "upstream_results": upstream_results,
                "subgraph_results": [],
            },
        )
        for entry in current_stage
    ]

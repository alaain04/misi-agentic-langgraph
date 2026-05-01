"""Task dispatcher — conditional edge function that fans out via Send."""

from langgraph.types import Send

from src.main_graph.constants import EXECUTE_PLAN
from src.main_graph.state import MainState


def task_dispatcher(state: MainState) -> list[Send]:
    """Return one Send per planned subgraph, carrying discovery context."""
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
                "subgraph_results": [],
            },
        )
        for name in state.get("plan", [])
    ]

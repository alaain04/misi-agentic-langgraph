"""Investigation planner node — wraps the service with graph config injection."""
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from src.main_graph.config import get_services
from src.main_graph.nodes.investigation_planner_service import investigation_planner_service
from src.main_graph.state import MainState


async def investigation_planner(state: MainState, config: RunnableConfig) -> dict | Command:
    svc = get_services(config)
    return await investigation_planner_service(state, svc["job_repo"], svc.get("vector_store"))

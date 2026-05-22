"""Orchestrator node."""

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from src.main_graph.config import get_services
from src.main_graph.nodes.orchestrator_service import orchestrator_service
from src.main_graph.state import MainState


async def orchestrator(state: MainState, config: RunnableConfig) -> dict | Command:
    svc = get_services(config)
    return await orchestrator_service(state, svc["job_repo"], svc["vector_store"])

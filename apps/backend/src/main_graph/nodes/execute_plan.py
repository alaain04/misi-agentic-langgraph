"""Execute plan node."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.nodes.execute_plan_service import execute_plan_service
from src.main_graph.state import MainState


async def execute_plan(state: MainState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    return await execute_plan_service(
        state,
        svc["job_repo"],
        svc["ingestion_daos"],
        dict(svc),
    )

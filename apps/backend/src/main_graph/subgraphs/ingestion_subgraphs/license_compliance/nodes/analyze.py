"""License compliance analysis node."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.service import (
    analyze_service,
)
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.state import (
    LicenseComplianceState,
)


async def analyze(state: LicenseComplianceState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    return await analyze_service(
        state,
        svc["container"],
        svc["ingestion_daos"]["license_compliance"],
    )

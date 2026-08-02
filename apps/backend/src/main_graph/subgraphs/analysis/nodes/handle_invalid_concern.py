from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.constants import ANALYSIS
from src.main_graph.subgraphs.analysis.state import AnalysisState

logger = logging.getLogger(__name__)

INVALID_CONCERN_MESSAGE = (
    "This request doesn't describe a dependency-risk concern this system can "
    "analyze. Try asking about one of: vulnerabilities, license compliance, "
    "dependency maintenance/staleness, supply-chain risk, or a related "
    "dependency research question. No analysis was run."
)


async def handle_invalid_concern(state: AnalysisState, config: RunnableConfig) -> dict:
    """Terminal node for a concern understand_concern classified as
    is_valid=False. Deliberately returns no analysis_result_id -- main_graph's
    existing _after_analysis routing (`if not analysis_result_id: return END`)
    already skips remediation/report for that case, and job_runner's
    _finalize already treats an early END here as JobStatus.done, not
    failed. The explanation is written as artifact data on the analysis
    node so the frontend can display it without a dedicated API field."""
    svc = get_services(config)
    logger.info(
        "handle_invalid_concern: job=%s concern=%r",
        state["job_id"],
        state["concern"],
    )
    await svc["job_repo"].update_artifact_data(
        state["job_id"], ANALYSIS, {"message": INVALID_CONCERN_MESSAGE}
    )
    return {}

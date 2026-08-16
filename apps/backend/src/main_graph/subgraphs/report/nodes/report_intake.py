from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.report.state import ReportState
from src.models.results import AnalysisResult
from src.utils.severity import filter_by_min_severity

logger = logging.getLogger(__name__)


async def report_intake(state: ReportState, config: RunnableConfig) -> dict:
    dao = get_services(config)["result_dao"]
    analysis: AnalysisResult = await dao.get_analysis(state["analysis_result_id"])

    findings = filter_by_min_severity(analysis.findings)
    dep_names = [f.dep_name for f in findings]

    logger.info(
        "report_intake: findings_to_enrich=%d (of %d total)",
        len(findings),
        len(analysis.findings),
    )
    return {
        "findings_to_enrich": [f.model_dump() for f in findings],
        "all_flagged_dep_names": dep_names,
    }

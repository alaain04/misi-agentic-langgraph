from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.report.agents.finding_enricher_agent import (
    enrich_finding,
)
from src.main_graph.subgraphs.report.state import ReportState
from src.models.conductor import FindingNote
from src.models.results import PrepResult

logger = logging.getLogger(__name__)


async def finding_enricher(state: ReportState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    prep: PrepResult = await dao.get_prep(state["prep_result_id"])
    finding = FindingNote(**state["current_finding"])
    all_flagged_dep_names = state.get("all_flagged_dep_names") or []

    logger.info(
        "finding_enricher: dep_name=%s severity=%s", finding.dep_name, finding.severity
    )

    draft, tools_used = await enrich_finding(
        finding, prep, all_flagged_dep_names, container
    )

    logger.info(
        "finding_enricher: dep_name=%s trust=%s tools_used=%s",
        finding.dep_name,
        draft.trust,
        tools_used,
    )
    return {"enriched_findings": [draft.model_dump()]}

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.constants import ANALYSIS
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.models.conductor import FindingNote
from src.models.results import AnalysisResult
from src.utils.config import settings
from src.utils.severity import filter_by_min_severity

logger = logging.getLogger(__name__)


def dedup_findings(findings: list[FindingNote]) -> list[FindingNote]:
    """Collapse byte-identical findings that a re-dispatched agent produced.

    A whole-tree agent (npm audit, license rules) returns its full finding set
    on every dispatch, so if the conductor re-dispatches it the same findings
    appear more than once. Key on (dep_name, severity, description): identical
    duplicates collapse, while genuinely distinct issues on the same package
    (different description) are preserved. Order-stable, keeps first occurrence.

    The key is provably safe for the whole-tree agents this bug is about, whose
    output is deterministic non-LLM text (npm audit / the SPDX rules table) —
    re-dispatch duplicates are byte-identical. It is theoretically weaker for
    LLM-narrative agents (maintenance, web_research): two genuinely distinct
    issues on one package at the same severity could produce identical
    description text and be collapsed. That is low-probability and still a
    strict improvement over the prior zero-dedup behavior; tightening the key
    for LLM-sourced findings (e.g. an evidence/advisory signature) is a possible
    follow-up if it is ever observed.
    """
    seen: set[tuple[str, str, str]] = set()
    result: list[FindingNote] = []
    for f in findings:
        key = (f.dep_name, f.severity, f.description)
        if key in seen:
            continue
        seen.add(key)
        result.append(f)
    return result


async def save_analysis_result(state: AnalysisState, config: RunnableConfig) -> dict:
    services = get_services(config)
    dao = services["result_dao"]
    job_repo = services["job_repo"]

    bundle_ids = state.get("bundle_ids") or []
    bundles = await dao.get_bundles(bundle_ids)

    all_findings = [f for b in bundles for f in b.findings]
    all_findings = dedup_findings(all_findings)
    all_findings = filter_by_min_severity(all_findings, settings.risk_min_severity)

    result = AnalysisResult(
        job_id=state["job_id"],
        concern=state["concern"],
        findings=all_findings,
        evidence_bundle_ids=bundle_ids,
        iteration_count=state.get("conductor_iteration") or 0,
    )
    analysis_result_id = await dao.save_analysis(result)

    await job_repo.update_artifact_data(
        state["job_id"], ANALYSIS, {"agent_calls": state.get("agent_calls") or []}
    )

    logger.info(
        "save_analysis_result: saved analysis_result_id=%s findings=%d",
        analysis_result_id,
        len(all_findings),
    )
    return {"analysis_result_id": analysis_result_id}

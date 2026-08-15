from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.constants import ANALYSIS
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.models.conductor import FindingNote
from src.models.results import AnalysisResult
from src.utils.severity import SEVERITY_ORDER, filter_by_min_severity

logger = logging.getLogger(__name__)


def dedup_findings(findings: list[FindingNote]) -> list[FindingNote]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[FindingNote] = []
    for f in findings:
        key = (f.dep_name, f.severity, f.description)
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)

    groups: dict[str, list[FindingNote]] = {}
    for f in unique:
        groups.setdefault(f.dep_name, []).append(f)

    result: list[FindingNote] = []
    for group in groups.values():
        if len(group) == 1:
            result.append(group[0])
            continue
        highest = max(group, key=lambda f: SEVERITY_ORDER.get(f.severity, 0))
        description = "\n".join(f"{f.severity} - {f.description}" for f in group)
        evidence = [e for f in group for e in f.evidence]
        update = {"description": description, "evidence": evidence}
        result.append(highest.model_copy(update=update))
    return result


async def save_analysis_result(state: AnalysisState, config: RunnableConfig) -> dict:
    services = get_services(config)
    dao = services["result_dao"]
    job_repo = services["job_repo"]

    bundle_ids = state.get("bundle_ids") or []
    bundles = await dao.get_bundles(bundle_ids)

    all_findings = [f for b in bundles for f in b.findings]
    all_findings = dedup_findings(all_findings)
    all_findings = filter_by_min_severity(all_findings)

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

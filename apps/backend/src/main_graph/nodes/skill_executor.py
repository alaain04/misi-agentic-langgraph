from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.skills.base import SkillContext
from src.main_graph.skills.registry import SKILL_REGISTRY
from src.main_graph.state import MainState
from src.models.evidence import Evidence

logger = logging.getLogger(__name__)


def _find_hypothesis_statement(state: MainState, hypothesis_id: str) -> str:
    plan = state.get("investigation_plan")
    if plan is None:
        return ""
    for h in plan.hypotheses:
        if h.id == hypothesis_id:
            return h.statement
    return ""


async def skill_executor(state: MainState, config: RunnableConfig) -> dict:
    skill_id = state.get("current_skill_id", "")
    dep_name = state.get("current_dep_name", "")
    hypothesis_id = state.get("current_hypothesis_id", "")

    skill = SKILL_REGISTRY.get(skill_id)
    if skill is None:
        logger.warning("skill_executor: unknown skill_id=%s", skill_id)
        return {"evidence": []}

    svc = get_services(config)
    ctx = SkillContext(
        dep_name=dep_name,
        hypothesis_id=hypothesis_id,
        hypothesis=_find_hypothesis_statement(state, hypothesis_id),
        sbom=state.get("sbom_cyclonedx") or {},
        repo_path=state.get("repo_path"),
        concern=state.get("concern", ""),
        services=svc,
    )

    try:
        evidence: list[Evidence] = await skill.execute(ctx)
    except Exception:
        logger.exception("skill_executor: skill=%s dep=%s failed", skill_id, dep_name)
        evidence = []

    logger.info("skill_executor: skill=%s dep=%s evidence_count=%d", skill_id, dep_name, len(evidence))
    return {"evidence": evidence}

from __future__ import annotations

import logging

from langgraph.types import Send

from src.main_graph.constants import EVIDENCE_COLLECTOR
from src.main_graph.skills.base import SkillContext
from src.main_graph.skills.registry import SKILL_REGISTRY
from src.main_graph.state import MainState

logger = logging.getLogger(__name__)


def _build_context_for_check(state: MainState, dep_name: str) -> SkillContext:
    return SkillContext(
        dep_name=dep_name,
        hypothesis_id="",
        hypothesis="",
        sbom=state.get("sbom_cyclonedx") or {},
        concern=state.get("concern", ""),
        repo_path=state.get("repo_path"),
        services={},
    )


def skill_dispatcher(state: MainState) -> list[Send] | str:
    plan = state.get("investigation_plan")
    if plan is None:
        return EVIDENCE_COLLECTOR

    sends = []
    for assignment in plan.skill_plan:
        skill = SKILL_REGISTRY.get(assignment.skill_id)
        if skill is None:
            continue
        check_ctx = _build_context_for_check(state, assignment.dep_name)
        if not skill.can_run(check_ctx):
            continue
        sends.append(Send("skill_executor", {
            **state,
            "current_skill_id": assignment.skill_id,
            "current_dep_name": assignment.dep_name,
            "current_hypothesis_id": assignment.hypothesis_id,
            "evidence": [],
        }))
    if not sends:
        logger.info("skill_dispatcher: no runnable skills, skipping to evidence_collector")
        return EVIDENCE_COLLECTOR
    logger.info("skill_dispatcher: dispatching %d skill tasks", len(sends))
    return sends

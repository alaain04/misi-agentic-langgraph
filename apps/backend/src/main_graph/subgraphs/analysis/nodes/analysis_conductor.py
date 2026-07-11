from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.models.results import AnalysisConductorDecision, PrepResult
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 4
_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM = """\
You are a dependency risk investigation conductor. Given a user concern and project context,
you dispatch domain specialist agents to gather evidence.

Output an AnalysisConductorDecision:
- dispatches: list of AgentDispatch (agents to launch in parallel)
- finalize: true when you have dispatched enough agents and collected sufficient evidence
- reasoning: explain your strategy

Available agent types: vulnerability_agent, maintenance_agent, supply_chain_agent, web_research_agent

Rules:
- First iteration: always dispatch at least 2 agents relevant to the concern.
- Subsequent iterations: review bundle summaries; dispatch follow-up agents only if gaps remain.
- Set finalize=true when confidence across all bundles is sufficient (usually after 1-2 rounds).
- Limit packages_to_focus to the most relevant packages (max 10) per dispatch.
- Use web_research_agent for concerns not covered by the static agents.
- After {max_iter} iterations, set finalize=true.
"""


def _format_bundles(bundles: list) -> str:
    if not bundles:
        return "No evidence collected yet."
    parts = []
    for b in bundles:
        parts.append(
            f"[{b.domain}] confidence={b.confidence:.2f}\n"
            f"  hypothesis: {b.hypothesis}\n"
            f"  summary: {b.summary}\n"
            f"  findings: {len(b.findings)}"
        )
    return "\n\n".join(parts)


async def analysis_conductor(state: AnalysisState, config: RunnableConfig) -> dict:
    iteration = (state.get("conductor_iteration") or 0) + 1
    dao = get_services(config)["result_dao"]

    prep: PrepResult = await dao.get_prep(state["prep_result_id"])

    bundle_ids = state.get("bundle_ids") or []
    bundles = await dao.get_bundles(bundle_ids) if bundle_ids else []

    user_prompt = (
        f"Concern: {state['concern']}\n\n"
        f"Project context:\n{prep.discovery_summary}\n\n"
        f"Package manager: {prep.detected_package_manager}\n"
        f"Direct dependencies: {list(prep.dependency_graph.get('direct', {}).keys())[:20]}\n\n"
        f"Evidence collected so far:\n{_format_bundles(bundles)}\n\n"
        f"Iteration: {iteration}/{_MAX_ITERATIONS}"
    )

    system = _SYSTEM.format(max_iter=_MAX_ITERATIONS)
    structured = _llm.with_structured_output(AnalysisConductorDecision, method="function_calling")
    decision: AnalysisConductorDecision = await structured.ainvoke([
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ])

    if iteration >= _MAX_ITERATIONS:
        decision = decision.model_copy(update={"finalize": True})

    logger.info(
        "analysis_conductor: iteration=%d dispatches=%d finalize=%s",
        iteration, len(decision.dispatches), decision.finalize,
    )
    return {"conductor_decision": decision, "conductor_iteration": iteration}

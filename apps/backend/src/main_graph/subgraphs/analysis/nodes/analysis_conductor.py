from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.analysis.agents.registry import get_agent_descriptions
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.models.results import AnalysisConductorDecision, PrepResult
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 4
_llm = get_llm(Model.GPT_5_4_MINI)


def _build_system(max_iter: int) -> str:
    roster = "\n".join(
        f"- {k}: {v}" for k, v in get_agent_descriptions().items()
    )
    return f"""\
You are a dependency risk investigation conductor for a Node.js project.
Your job: given a user concern and project context, dispatch the right specialist agents
to collect evidence, then finalize once you have enough to produce a risk report.

Output an AnalysisConductorDecision:
- dispatches: list of AgentDispatch (agents to run in parallel this iteration)
- finalize: true when evidence is sufficient to produce a complete risk report
- reasoning: explain which gaps remain and why you are or are not finalizing

Available agents:
{roster}

Dispatch strategy:
- Map the concern to the agents whose description matches it best.
  * "vulnerability" / "CVE" / "exploit" → vulnerability_agent first
  * "outdated" / "unmaintained" / "deprecated" → maintenance_agent first
  * "supply chain" / "typosquat" / "malicious" → supply_chain_agent first
  * Recent news, novel threats, or anything not covered above → web_research_agent
- First iteration: dispatch at least 2 agents that directly address the concern.
- Subsequent iterations: only dispatch follow-up agents when a specific gap remains
  (e.g. a flagged package not yet inspected, a lead from one agent worth pursuing).
  Do not re-dispatch the same agent with the same hypothesis — that is wasted effort.

Packages to focus: pick the most directly relevant packages from the dependency list
(max 10 per dispatch). Prefer direct dependencies over transitive ones.

Finalize when:
- All agents relevant to the concern have reported with confidence >= 0.6, OR
- Two rounds of agents produced consistent findings with no new leads, OR
- Iteration {max_iter} is reached.
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

    system = _build_system(_MAX_ITERATIONS)
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

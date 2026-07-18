from __future__ import annotations

import logging
import textwrap

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.analysis.agents.registry import get_agent_descriptions
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.models.results import AnalysisConductorDecision, PrepResult
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 4
_llm = get_llm(Model.GPT_5_4_MINI)


_SYSTEM_TEMPLATE = textwrap.dedent("""\
    You are a dependency risk investigation conductor for a Node.js project.
    Your job: given a user concern and project context, dispatch the right
    specialist agents to collect evidence, then finalize once you have enough
    to produce a risk report.

    Output an AnalysisConductorDecision:
    - dispatches: list of AgentDispatch (agents to run in parallel this iteration)
    - finalize: true when evidence is sufficient to produce a complete risk report
    - reasoning: explain which gaps remain and why you are or are not finalizing

    Available agents:
    {roster}

    Dispatch strategy:
    - Select agents by capability match to the concern. Dispatch as few or as many
      as the concern needs (1 to all of them) — there is no minimum or maximum count.
    - You may dispatch the SAME agent multiple times in parallel. Two common cases:
      (a) shard a large package set — same hypothesis, different packages_to_focus;
      (b) probe a different angle on the same packages — different hypothesis.
      Keep total parallel dispatches per iteration <= 5.
    - Every dispatch must be unique across (agent_type, packages_to_focus, hypothesis).
      Never re-run a combination already answered in the evidence collected so far.
    - The vulnerability_agent audits the ENTIRE dependency tree in one run. Dispatch
      it at most once, leave packages_to_focus empty for it, and never shard it —
      extra dispatches add no coverage.
    - Later iterations: dispatch only to close a specific gap or chase a lead from a
      prior bundle. Spend early iterations on breadth, later ones on depth.

    Package selection:
    - Choose packages by relevance to the concern, not by count.
    - Direct dependencies first for maintenance/general concerns; for supply-chain
      and vulnerability concerns, transitive dependencies are in scope and often
      higher risk.

    Finalize when:
    - All agents relevant to the concern have reported with confidence >= 0.6, OR
    - Two rounds of agents produced consistent findings with no new leads, OR
    - Iteration {max_iter} is reached.

    A bundle marked "unresolved" failed evidence verification: treat it as an open gap.
    Prefer re-dispatching to close it, or discount its findings when finalizing.
    """).strip()


def _build_system(max_iter: int) -> str:
    roster = "\n".join(f"- {k}: {v}" for k, v in get_agent_descriptions().items())
    return _SYSTEM_TEMPLATE.format(roster=roster, max_iter=max_iter)


def _format_bundles(bundles: list) -> str:
    if not bundles:
        return "No evidence collected yet."
    parts = []
    for b in bundles:
        packages = ", ".join(b.packages_to_focus) or "n/a"
        block = (
            f"[{b.domain}] confidence={b.confidence:.2f}\n"
            f"  hypothesis: {b.hypothesis}\n"
            f"  packages: {packages}\n"
            f"  summary: {b.summary}\n"
            f"  findings: {len(b.findings)}"
        )
        if getattr(b, "verification_note", None):
            block += f"\n  unresolved: {b.verification_note}"
        parts.append(block)
    return "\n\n".join(parts)


async def analysis_conductor(state: AnalysisState, config: RunnableConfig) -> dict:
    iteration = (state.get("conductor_iteration") or 0) + 1
    dao = get_services(config)["result_dao"]

    prep: PrepResult = await dao.get_prep(state["prep_result_id"])

    bundle_ids = state.get("bundle_ids") or []
    bundles = await dao.get_bundles(bundle_ids) if bundle_ids else []

    direct_versions = [
        f"{n}@{v}" for n, v in prep.dependency_graph.get("direct", {}).items()
    ]
    user_prompt = (
        f"Concern: {state['concern']}\n\n"
        f"Project context:\n{prep.discovery_summary}\n\n"
        f"Package manager: {prep.detected_package_manager}\n"
        f"Direct dependencies (name@installed_version): {direct_versions}\n\n"
        f"Evidence collected so far:\n{_format_bundles(bundles)}\n\n"
        f"Iteration: {iteration}/{_MAX_ITERATIONS}"
    )

    system = _build_system(_MAX_ITERATIONS)
    structured = _llm.with_structured_output(
        AnalysisConductorDecision, method="function_calling"
    )
    decision: AnalysisConductorDecision = await structured.ainvoke(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
    )

    if iteration >= _MAX_ITERATIONS:
        decision = decision.model_copy(update={"finalize": True})

    logger.info(
        "analysis_conductor: iteration=%d dispatches=%d finalize=%s",
        iteration,
        len(decision.dispatches),
        decision.finalize,
    )
    return {"conductor_decision": decision, "conductor_iteration": iteration}

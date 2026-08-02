"""The three nodes that replace analysis_conductor / _after_conductor /
domain_agent / evidence_collector (spec D1)."""

from __future__ import annotations

import textwrap

from deepagents import create_deep_agent
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.analysis.agents.registry import (
    REGISTRY,
    get_agent_descriptions,
)
from src.main_graph.subgraphs.analysis.deepagent.backstop import (
    deterministic_backstop_dispatch,
)
from src.main_graph.subgraphs.analysis.deepagent.coverage import (
    WHOLE_TREE_AGENT_TYPES,
    compute_missing_direct_deps,
    whole_tree_scan_satisfies_concern,
)
from src.main_graph.subgraphs.analysis.deepagent.limits import DEEPAGENT_LIMITS
from src.main_graph.subgraphs.analysis.deepagent.state import AnalysisDeepAgentState
from src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper import (
    build_agent_subagent,
)
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.utils.llm import Model, get_llm

_MAX_CORRECTION_ROUNDS = 2
_RECURSION_LIMIT = 50

_SYSTEM_TEMPLATE = textwrap.dedent("""\
    You are a dependency risk investigation agent for a Node.js project. You
    are invoked only when whole-tree scanning alone cannot fully answer the
    concern -- a deterministic router already ran vulnerability_agent and/or
    license_agent directly wherever they applied.

    Your goal: produce a complete answer while minimizing specialist calls.
    Every call costs latency, tokens, and budget.
    Prefer the smallest plan that completely answers the concern.
    You have a hard budget of {max_specialist_calls} specialist calls, at
    most {max_parallel_calls} running concurrently.

    Available specialists (call via the task tool):
    {roster}
    {already_done}

    Before every dispatch, ask: "What new information will this provide
    that's necessary for the final report?" If the answer is "none," "only
    confirms an existing finding," or "would just be interesting to know,"
    don't dispatch -- for example, never delegate to web_research_agent just
    to re-find CVEs vulnerability_agent already reported.

    For every package-scoped specialist you use, cover every direct
    dependency relevant to the concern -- you may be asked to cover specific
    missing ones if you stop early.

    Stop when every required question has evidence, no gap remains, and
    further specialists would only add confidence rather than change
    conclusions.

    If you would exceed your budget, prioritize the highest-risk
    dependencies first and report which packages remain unanalyzed.

    Direct dependencies (name@installed_version): {direct_deps}
    Concern: {concern} (type={concern_type}, scope={concern_scope})
    """).strip()


def _roster(exclude: set[str] | None = None) -> str:
    exclude = exclude or set()
    return "\n".join(
        f"- {k}: {v}" for k, v in get_agent_descriptions().items() if k not in exclude
    )


def _build_deep_agent():
    subagents = [build_agent_subagent(agent_type) for agent_type in REGISTRY]
    return create_deep_agent(
        model=get_llm(Model.GPT_5_4_MINI),
        subagents=subagents,
        state_schema=AnalysisDeepAgentState,
    )


_deep_agent = _build_deep_agent()


async def analysis_deepagent_node(state: AnalysisState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    prep = await dao.get_prep(state["prep_result_id"])

    deepagent_state = state.get("deepagent_state")
    if deepagent_state is None:
        direct_deps = [
            f"{n}@{v}" for n, v in prep.dependency_graph.get("direct", {}).items()
        ]
        structured_concern = state["structured_concern"]
        # A whole-tree agent may already have run via run_direct_agents (the
        # router's mandatory prefix step) before this concern was ever
        # classified as complex -- e.g. a mixed concern like
        # type=["vulnerability", "maintenance"] gets its vulnerability_agent
        # portion peeled off before the deep agent starts. Exclude those
        # agents from the roster so the deep agent doesn't spend a planning
        # turn re-deciding whether to invoke them.
        already_run = sorted(
            {
                c["agent_type"]
                for c in (state.get("agent_calls") or [])
                if c.get("agent_type") in WHOLE_TREE_AGENT_TYPES
            }
        )
        already_done_note = (
            f"Already completed for this concern: {already_run} -- do not "
            "dispatch these again; focus on the remaining investigation."
            if already_run
            else ""
        )
        system = _SYSTEM_TEMPLATE.format(
            roster=_roster(exclude=set(already_run)),
            already_done=already_done_note,
            direct_deps=direct_deps,
            concern=state["concern"],
            concern_type=structured_concern["type"],
            concern_scope=structured_concern["scope"],
            max_specialist_calls=DEEPAGENT_LIMITS.max_specialist_calls,
            max_parallel_calls=DEEPAGENT_LIMITS.max_parallel_calls,
        )
        # Seed from the outer state's already-accumulated bundle_ids/agent_calls
        # (e.g. from the run_direct_agents prefix) so the D8 whole-tree dedup
        # in subagent_wrapper._run recognizes prior work even if the deep
        # agent's LLM ignores the prompt's already_done note and tries to
        # dispatch an already-run whole-tree agent anyway -- it becomes a
        # no-op instead of a real re-run. The existing delta-slicing below
        # (prev_bundle_ids/prev_call_bundle_ids) already handles excluding
        # these seeded entries from what gets reported as new this round, the
        # same mechanism that already handles cross-round re-emission.
        deepagent_state = {
            "messages": [HumanMessage(content=system)],
            "job_id": state["job_id"],
            "prep_result_id": state["prep_result_id"],
            "bundle_ids": list(state.get("bundle_ids") or []),
            "agent_calls": list(state.get("agent_calls") or []),
        }
    else:
        missing = state.get("missing_deps") or []
        deepagent_state["messages"] = [
            *deepagent_state["messages"],
            HumanMessage(
                content=(
                    "These direct dependencies still need coverage before "
                    f"you finalize: {missing}"
                )
            ),
        ]

    # Preserve full state; return only new entries to prevent double-counting.
    prev_bundle_ids = set(deepagent_state.get("bundle_ids") or [])
    prev_call_bundle_ids = {
        c.get("bundle_id") for c in (deepagent_state.get("agent_calls") or [])
    }

    run_config = {**config, "recursion_limit": _RECURSION_LIMIT}
    result = await _deep_agent.ainvoke(deepagent_state, run_config)

    seen_bundle_ids = set(prev_bundle_ids)
    new_bundle_ids: list[str] = []
    for bundle_id in result.get("bundle_ids") or []:
        if bundle_id not in seen_bundle_ids:
            seen_bundle_ids.add(bundle_id)
            new_bundle_ids.append(bundle_id)

    seen_call_bundle_ids = set(prev_call_bundle_ids)
    new_agent_calls: list[dict] = []
    for call in result.get("agent_calls") or []:
        bundle_id = call.get("bundle_id")
        if bundle_id not in seen_call_bundle_ids:
            seen_call_bundle_ids.add(bundle_id)
            new_agent_calls.append(call)

    return {
        "deepagent_state": result,
        "bundle_ids": new_bundle_ids,
        "agent_calls": new_agent_calls,
    }


async def coverage_gate(state: AnalysisState, config: RunnableConfig) -> dict:
    requires_per_dependency_analysis = (state.get("structured_concern") or {}).get(
        "requires_per_dependency_analysis", True
    )
    if not requires_per_dependency_analysis:
        return {
            "missing_deps": [],
            "correction_rounds": (state.get("correction_rounds") or 0) + 1,
        }

    svc = get_services(config)
    dao = svc["result_dao"]
    prep = await dao.get_prep(state["prep_result_id"])
    agent_calls = state.get("agent_calls") or []

    whole_tree_calls = [
        c for c in agent_calls if c.get("agent_type") in WHOLE_TREE_AGENT_TYPES
    ]
    bundle_id_by_type = {c["agent_type"]: c["bundle_id"] for c in whole_tree_calls}
    bundles = (
        await dao.get_bundles(list(bundle_id_by_type.values()))
        if bundle_id_by_type
        else []
    )
    bundle_by_id = {b.id: b for b in bundles}
    successful_roster = sorted(
        agent_type
        for agent_type, bundle_id in bundle_id_by_type.items()
        if (bundle := bundle_by_id.get(bundle_id)) is not None
        and bundle.confidence > 0.5
    )

    checked_roster = state.get("whole_tree_checked_roster")
    if successful_roster and successful_roster != checked_roster:
        satisfies = await whole_tree_scan_satisfies_concern(
            state["concern"], successful_roster
        )
    else:
        satisfies = state.get("whole_tree_satisfies_concern", False)

    if satisfies:
        missing: list[str] = []
    else:
        direct_deps = list(prep.dependency_graph.get("direct", {}).keys())
        missing = compute_missing_direct_deps(agent_calls, direct_deps)

    return {
        "missing_deps": missing,
        "correction_rounds": (state.get("correction_rounds") or 0) + 1,
        "whole_tree_checked_roster": successful_roster,
        "whole_tree_satisfies_concern": satisfies,
    }


def route_after_coverage_gate(state: AnalysisState) -> str:
    missing = state.get("missing_deps") or []
    if not missing:
        return "save_analysis_result"
    if (state.get("correction_rounds") or 0) <= _MAX_CORRECTION_ROUNDS:
        return "analysis_deepagent_node"
    return "backstop_dispatch"


async def backstop_dispatch_node(state: AnalysisState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    prep = await dao.get_prep(state["prep_result_id"])

    bundle_ids, new_calls = await deterministic_backstop_dispatch(
        missing_deps=state.get("missing_deps") or [],
        agent_calls=state.get("agent_calls") or [],
        prep=prep,
        container=svc["container"],
        dao=dao,
        cache=svc.get("input_cache"),
        concern=state["concern"],
    )
    return {"bundle_ids": bundle_ids, "agent_calls": new_calls}

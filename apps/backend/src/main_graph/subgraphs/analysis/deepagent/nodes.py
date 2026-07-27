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
    compute_missing_direct_deps,
)
from src.main_graph.subgraphs.analysis.deepagent.state import AnalysisDeepAgentState
from src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper import (
    build_agent_subagent,
)
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.utils.llm import Model, get_llm

_MAX_CORRECTION_ROUNDS = 2

_SYSTEM_TEMPLATE = textwrap.dedent("""\
    You are a dependency risk investigation agent for a Node.js project.
    Your job: given a user concern and project context, delegate to the right
    specialist subagents to collect evidence, then stop once you have enough
    evidence to support a complete risk report.

    Available specialists (call via the task tool):
    {roster}

    - Delegate to a subagent as many or as few times as the concern needs.
    - You may delegate to the same specialist multiple times with different
      packages or a different angle.
    - vulnerability_agent and license_agent each scan the ENTIRE dependency
      tree in a single run -- delegate to each at most once.
    - For every other specialist, make sure your delegated tasks collectively
      cover every direct dependency relevant to the concern -- you may be
      asked to cover specific missing ones if you stop early.

    Direct dependencies (name@installed_version): {direct_deps}
    Concern: {concern}
    Project context: {context}
    """).strip()


def _roster() -> str:
    return "\n".join(f"- {k}: {v}" for k, v in get_agent_descriptions().items())


_RECURSION_LIMIT = 50
"""Hard backstop per spec D6 -- the same role _MAX_ITERATIONS plays for the
old conductor. CompiledSubAgents can't themselves spawn further subagents
(flat one-node graphs, see subagent_wrapper.py), so this only bounds the
root deep agent's own step count, not an unbounded recursive fan-out."""


def _build_deep_agent():
    # Spec D3: deliberately no `tools=` / `middleware=[CodeInterpreterMiddleware(...)]`
    # here. The root agent's only tools are task() dispatch to the five
    # CompiledSubAgents plus deepagents' own built-in filesystem/todo tools --
    # no execute_command-class tool is reachable from this agent or any
    # subagent. Do not add one without re-opening the spec's D3 decision.
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
        system = _SYSTEM_TEMPLATE.format(
            roster=_roster(),
            direct_deps=direct_deps,
            concern=state["concern"],
            context=prep.discovery_summary[:1000],
        )
        deepagent_state = {
            "messages": [HumanMessage(content=system)],
            "job_id": state["job_id"],
            "prep_result_id": state["prep_result_id"],
            "bundle_ids": [],
            "agent_calls": [],
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

    # deepagent_state carries the FULL accumulated bundle_ids/agent_calls
    # forward across correction rounds -- required so subagent_wrapper's D8
    # whole-tree dedup check (which reads state["agent_calls"] at the moment
    # task() fires) still sees round 1's calls in round 2. But AnalysisState's
    # own bundle_ids/agent_calls also use an operator.add reducer, so if we
    # returned the FULL accumulated lists again this round, the outer state
    # would double-count everything already reported after round 1. Return only
    # the genuinely-new bundles/calls this round produced.
    #
    # We diff by bundle_id (a fresh uuid per saved bundle), NOT by list index:
    # re-invoking the deep agent with the carried-forward messages re-emits the
    # earlier rounds' task() Command(update=...) into the accumulator (verified
    # against deepagents 0.6.12 -- the prior round's bundle_id/agent_call
    # reappear in `result` even though no subagent re-ran), so a positional
    # `[prev_count:]` slice would re-report an already-counted bundle. Set
    # membership on bundle_id is immune to that reordering/re-emission.
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
    svc = get_services(config)
    prep = await svc["result_dao"].get_prep(state["prep_result_id"])
    direct_deps = list(prep.dependency_graph.get("direct", {}).keys())

    missing = compute_missing_direct_deps(state.get("agent_calls") or [], direct_deps)
    return {
        "missing_deps": missing,
        "correction_rounds": (state.get("correction_rounds") or 0) + 1,
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

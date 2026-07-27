"""Builds the single reusable per-target CompiledSubAgent (spec D2).

The root deep agent communicates a target as free text (deepagents' task()
tool has no way to pass a typed value), so this node's first step is a
small structured-output call converting that text back into a target dep
name - same pattern as the analysis-subgraph swap's
_extract_dispatch/AgentDispatch, applied here for a bare string instead of
a richer type.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, cast

from deepagents import CompiledSubAgent, create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel
from typing_extensions import TypedDict

from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.deepagent.state import _merge_replace
from src.main_graph.subgraphs.remediation.deepagent.tools import (
    make_bump_dependency_tool,
    make_dependents_of_tool,
    make_read_release_notes_tool,
    make_verify_tool,
)
from src.main_graph.subgraphs.remediation.workspace import copy_repo
from src.main_graph.tools.blast_radius import make_blast_radius_tool
from src.main_graph.tools.search_code import make_search_code_tool
from src.models.remediation import Remediation, RemediationOutcome, RemediationTarget
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM_PROMPT = """\
You are remediating ONE dependency risk in a Node.js project: {target_dep}
(currently {current_range}).

Findings this addresses: {addresses}

Evidence (npm audit fix paths, outdated versions):
{evidence}

Steps:
1. Call read_release_notes to review what changed between the installed
   version and reasonable upgrade candidates.
2. Call blast_radius and search_code to see how {target_dep} is actually
   used in this codebase.
3. Decide: if nothing relevant broke, bump only. If something broke but you
   can fix the call sites yourself, bump AND edit the affected files. If
   the dependency itself should be replaced (abandoned, superseded, or the
   evidence above already says so), propose a replacement and migrate all
   usage yourself.
4. If your investigation shows another dependency must also move for this
   fix to be coherent (e.g. a peer/plugin no longer compatible), call
   dependents_of to confirm it is really in this tree, then list it in
   `requires` on your final answer - do not try to fix it yourself.
5. Apply your change with bump_dependency and/or direct file edits, then
   call verify. Iterate until satisfied or you conclude there is no safe
   fix - your own verify result guides your next step, it is not the final
   word on whether this ships.
6. Finish with your structured answer, including a short `summary` and, if
   you made file edits, the unified diff of those edits in `code_diff`.
"""


class _TargetDepExtraction(BaseModel):
    target_dep: str


async def _extract_target_dep(description: str, known_targets: list[str]) -> str:
    structured = _llm.with_structured_output(
        _TargetDepExtraction, method="function_calling"
    )
    result = cast(
        _TargetDepExtraction,
        await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": (
                        "Extract which npm package this remediation task "
                        "description is about. Known open targets: "
                        f"{', '.join(known_targets) or 'none yet'}."
                    ),
                },
                {"role": "user", "content": description},
            ]
        ),
    )
    return result.target_dep


class _TargetSubagentState(TypedDict):
    messages: list
    job_id: str
    prep_result_id: str
    evidence: dict
    targets: dict[str, dict]
    remediations: Annotated[dict[str, dict], _merge_replace]
    requires_edges: Annotated[dict[str, list], _merge_replace]


async def _run(state: _TargetSubagentState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    prep = await dao.get_prep(state["prep_result_id"])

    targets = state.get("targets") or {}
    last_message = state["messages"][-1]
    # `messages` carries no add_messages reducer on this state schema (see
    # _TargetSubagentState below), so LangGraph passes through whatever the
    # caller supplied verbatim: a real BaseMessage instance when invoked
    # inside deepagents' task() tool, or a plain {"role", "content"} dict
    # when a caller (e.g. this module's own unit tests) builds state by
    # hand. Handle both rather than assuming one.
    task_description = str(
        last_message.get("content")
        if isinstance(last_message, dict)
        else last_message.content
    )
    target_dep = await _extract_target_dep(task_description, list(targets))

    target_dict = targets.get(target_dep)
    if target_dict is not None:
        target = RemediationTarget(**target_dict)
    else:
        current_range = (prep.dependency_graph.get("direct") or {}).get(target_dep)
        target = RemediationTarget(
            target_dep=target_dep, addresses=[], current_range=current_range
        )

    work_dir = copy_repo(prep.repo_path)
    default_targeted = [target.target_dep, *target.addresses]
    tools = [
        make_read_release_notes_tool(work_dir, container, prep.docker_image),
        make_blast_radius_tool(work_dir, container, prep.docker_image),
        make_dependents_of_tool(prep.dependency_graph),
        make_bump_dependency_tool(work_dir),
        make_verify_tool(
            work_dir,
            container,
            prep.docker_image,
            prep.detected_package_manager,
            default_targeted,
        ),
    ]
    if prep.vector_store_id:
        tools.append(make_search_code_tool(prep.vector_store_id))

    nested = create_deep_agent(
        model=get_llm(Model.GPT_5_4_MINI),
        tools=tools,
        system_prompt=_SYSTEM_PROMPT.format(
            target_dep=target.target_dep,
            current_range=target.current_range or "unknown",
            addresses=", ".join(target.addresses)
            or (
                "none (this dependency was pulled in because remediating "
                "another target requires it)"
            ),
            evidence=json.dumps(state.get("evidence") or {})[:4000],
        ),
        backend=FilesystemBackend(root_dir=work_dir),
        response_format=RemediationOutcome,
    )
    result = await nested.ainvoke(
        {"messages": [{"role": "user", "content": f"Remediate {target.target_dep}."}]},
        config,
    )
    raw_outcome = result.get("structured_response")
    outcome: RemediationOutcome | None
    if isinstance(raw_outcome, RemediationOutcome):
        outcome = raw_outcome
    elif raw_outcome is not None:
        outcome = RemediationOutcome.model_validate(raw_outcome)
    else:
        outcome = None

    if outcome is None:
        remediation = Remediation(
            addresses=target.addresses,
            target_dep=target.target_dep,
            from_range=target.current_range,
            status="failed",
            skip_reason="agent produced no structured decision",
        )
        return {
            "messages": [],
            "remediations": {target.target_dep: remediation.model_dump()},
            "requires_edges": {},
        }

    remediation = Remediation(
        addresses=target.addresses,
        target_dep=target.target_dep,
        strategy=outcome.strategy,
        from_range=target.current_range,
        to_range=outcome.to_range,
        replacement_dep=outcome.replacement_dep,
        replacement_range=outcome.replacement_range,
        migration_plan=outcome.migration_plan,
        patch=outcome.code_diff,
        status="skipped",  # provisional - group_and_verify_gate sets the real value
        skip_reason=outcome.skip_reason,
    )
    requires_edges = {target.target_dep: outcome.requires} if outcome.requires else {}
    return {
        "messages": [],
        "remediations": {target.target_dep: remediation.model_dump()},
        "requires_edges": requires_edges,
    }


def build_target_subagent() -> CompiledSubAgent:
    graph = StateGraph(_TargetSubagentState)
    graph.add_node("run", _run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return {
        "name": "remediate_target",
        "description": (
            "Investigate and remediate ONE dependency risk. Describe which "
            "dependency to work on by name. Reviews release notes and real "
            "usage, decides bump vs. bump-and-adapt-code vs. replace, edits "
            "files and verifies its own work, and reports a structured "
            "outcome including any OTHER dependency that must also move "
            "for this fix to be coherent."
        ),
        "runnable": graph.compile(),
    }

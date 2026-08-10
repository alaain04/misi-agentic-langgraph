"""The single execution agent dispatched by remediate_targets_node (spec
2026-08-08-remediation-flatten-planning-execution). One flat deep agent per
connected group of targets, invoked directly -- never through deepagents'
`task()` tool, so there is no agent dispatching another agent. It handles
both `bump`-hinted and `codemod`-hinted targets in the same loop: a bump
task is expected to just call bump_dependency + verify, but the agent can
escalate to search/edit for any target if verify surfaces a problem the
release digest missed."""

from __future__ import annotations

from typing import Annotated

from deepagents import DeepAgentState, create_deep_agent
from deepagents.backends import FilesystemBackend

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.remediation.deepagent.limits import (
    MAX_RETRIES,
    REMEDIATION_RATE_LIMITER,
)
from src.main_graph.subgraphs.remediation.deepagent.state import _merge_replace
from src.main_graph.subgraphs.remediation.deepagent.tools import (
    make_bump_dependency_tool,
    make_commit_outcome_tool,
    make_read_release_notes_tool,
    make_verify_tool,
)
from src.main_graph.tools.blast_radius import make_blast_radius_tool
from src.main_graph.tools.search_code import make_search_code_tool
from src.utils.llm import get_llm
from src.utils.model_registry import AgentRole, resolve_model


class ExecutionState(DeepAgentState):
    outcomes: Annotated[dict[str, dict], _merge_replace]


EXECUTION_PROMPT = """\
You execute dependency remediation for a Node.js project. You are given one \
or more targets that must be handled together in this working copy (they \
are coupled by a companion-dependency requirement, or this is the only \
target in its group). For each target you are given its migration plan's \
task kind (bump or codemod) and its migration guide, if any.

For a target whose plan is bump-only: call bump_dependency, then verify. If \
verify passes, you are done with that target -- do not search or edit its \
code. Only if verify fails, or the plan already includes a codemod task, \
use search_code and read_release_notes to find what needs to change and \
edit the minimum necessary call sites, iterating with verify until it is \
green or you conclude there is no safe fix.

Finish EVERY target listed by calling commit_outcome with its target_dep \
and a RemediationOutcome: strategy ("bump" if you never edited source for \
this target, "bump_with_codemod" if you did), to_range, code_diff (unified \
diff of source files only -- NOT package.json -- empty if you made no code \
edits), a short summary, and status/skip_reason if you could not produce a \
safe fix. commit_outcome is the only thing that ships your work for a \
target; a summary alone is discarded."""


def build_execution_agent(
    work_dir: str,
    container: ContainerRunPort,
    docker_image: str,
    package_manager: str,
):
    """Compile the flat execution agent for one group. Callers invoke the
    returned agent's `.ainvoke` directly (see remediate_targets_node) -- it
    is not registered as a deepagents `subagents=[...]` entry, so nothing
    can dispatch it via `task()`."""
    tools = [
        make_read_release_notes_tool(work_dir, container, docker_image),
        make_blast_radius_tool(work_dir, container, docker_image),
        make_search_code_tool(work_dir, container, docker_image),
        make_bump_dependency_tool(work_dir),
        make_verify_tool(work_dir, container, docker_image, package_manager, []),
        make_commit_outcome_tool(),
    ]
    # Use resolve_model() + get_llm() instead of get_role_llm() to avoid wrapping.
    # The latter returns a RunnableBinding (via .with_config()), which isn't a
    # BaseChatModel and breaks create_deep_agent's model resolution. Tag the
    # compiled graph instead.
    agent = create_deep_agent(
        model=get_llm(
            resolve_model(AgentRole.REMEDIATION_EXECUTION_DEEPAGENT),
            rate_limiter=REMEDIATION_RATE_LIMITER,
            max_retries=MAX_RETRIES,
        ),
        tools=tools,
        system_prompt=EXECUTION_PROMPT,
        backend=FilesystemBackend(root_dir=work_dir, virtual_mode=True),
        state_schema=ExecutionState,
    )
    return agent.with_config(
        tags=[f"agent_role:{AgentRole.REMEDIATION_EXECUTION_DEEPAGENT.value}"]
    )

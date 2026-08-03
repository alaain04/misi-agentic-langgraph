"""Typed implementation subagents dispatched by plan_and_orchestrate
(spec D4). codemod_adapter is a sandboxed deepagent that adapts call sites;
replacement_migrator is a Spec-A stub (real r3 is Spec B)."""

from __future__ import annotations

from typing import NotRequired

from deepagents import CompiledSubAgent, create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.remediation.deepagent.tools import (
    make_bump_dependency_tool,
    make_read_release_notes_tool,
    make_verify_tool,
)
from src.main_graph.tools.blast_radius import make_blast_radius_tool
from src.main_graph.tools.search_code import make_search_code_tool
from src.models.remediation import RemediationOutcome
from src.utils.llm import Model, get_llm

_CODEMOD_PROMPT = """\
You adapt this Node.js project's own source to a dependency upgrade that has
a known breaking change. You are given the migration guide and the specific
files that use the dependency. Edit ONLY what the guide requires, then call
verify. Iterate until verify is green or you conclude there is no safe fix.
Finish with a structured RemediationOutcome including the unified diff of your
edits in code_diff and a short summary."""


def build_codemod_subagent(
    work_dir: str,
    container: ContainerRunPort,
    docker_image: str,
    package_manager: str,
) -> CompiledSubAgent:
    tools = [
        make_read_release_notes_tool(work_dir, container, docker_image),
        make_blast_radius_tool(work_dir, container, docker_image),
        make_search_code_tool(work_dir, container, docker_image),
        make_bump_dependency_tool(work_dir),
        make_verify_tool(work_dir, container, docker_image, package_manager, []),
    ]
    agent = create_deep_agent(
        model=get_llm(Model.GPT_5_4_MINI),
        tools=tools,
        system_prompt=_CODEMOD_PROMPT,
        backend=FilesystemBackend(root_dir=work_dir, virtual_mode=True),
        response_format=RemediationOutcome,
    )
    return {
        "name": "codemod_adapter",
        "description": (
            "Adapt this project's call sites to a breaking dependency change. "
            "Give it the migration guide and the affected files."
        ),
        "runnable": agent,
    }


class _StubState(TypedDict):
    messages: list
    structured_response: NotRequired[RemediationOutcome]


async def _replacement_stub(state: _StubState, config: RunnableConfig) -> dict:
    outcome = RemediationOutcome(
        strategy="replace",
        status="skipped",
        skip_reason="dependency replacement deferred (Spec B)",
        summary="replacement not implemented in this build",
    )
    return {"messages": [], "structured_response": outcome}


def build_replacement_subagent(
    work_dir: str,
    container: ContainerRunPort,
    docker_image: str,
    package_manager: str,
) -> CompiledSubAgent:
    graph = StateGraph(_StubState)
    graph.add_node("run", _replacement_stub)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return {
        "name": "replacement_migrator",
        "description": (
            "Replace a dependency with a different package and migrate usage. "
            "Deferred in this build; reports skipped."
        ),
        "runnable": graph.compile(),
    }

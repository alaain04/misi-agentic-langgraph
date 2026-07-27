"""Blackbox integration tests for the deepagent-based remediation subgraph.

Requires Docker. Run with:
    uv run pytest tests/subgraphs/test_remediation_subgraph.py -v

What is real:
- LangGraph wiring: root_deepagent_node -> group_and_verify_gate ->
  (loop | pr_and_persist_node)
- The real `deepagents` `create_deep_agent` machinery for BOTH the root
  coordinator agent and each per-target nested agent (task() dispatch,
  ToolNode execution, structured_response extraction via ToolStrategy).
- `RemediationDeepAgentState`'s reducers (_keep_first_str/_keep_first_dict/
  _merge_replace), exercised under two concurrent task() calls landing in
  one root superstep (test 3) -- the same bug class the analysis-subgraph
  swap hit for real once (InvalidUpdateError: "Can receive only one value
  per step").
- `FilesystemBackend(root_dir=work_dir, virtual_mode=True)`: test 1 has the
  nested worker actually call the real `read_file`/`write_file` tools
  against a real isolated `copy_repo` clone on disk, and asserts the file
  landed on disk with the expected content.
- `connected_groups`, `group_and_verify_gate`'s verification/retry logic,
  `pr_and_persist_node`'s PR-title/consent gating, and real MongoDB
  persistence (result_dao, via the session testcontainer).

What is mocked (three real LLM call sites, replaced with scripted fake chat
models -- same structural shape as the analysis-subgraph swap):
- The root coordinator's own model (scripted task() tool calls; content-
  routed off the running set of already-dispatched dep names, since the
  ToolMessage the task() tool feeds back to the root is empty for our
  custom `remediate_target` subgraph -- see the SPIKE note below).
- Each nested per-target worker's own model (scripted RemediationOutcome
  tool calls, content-routed off the target dep name parsed out of the
  worker's own system prompt).
- `subagent_wrapper._extract_target_dep` (trivial echo -- see below).
Also patched, per the task-10 brief, purely as network/subprocess
containment (not because any test exercises their content):
- `asyncio.create_subprocess_exec` at the `deepagent.tools` module path,
  so `read_release_notes` can never reach the real `gh` CLI/network.
- `subgraph_config`'s `container` mock (real MongoDB, fake container.run).

SPIKE FINDINGS (required by the task-10 brief before writing the full
suite; this settles the open question flagged by task 6's
subagent_wrapper.py and task 8's nodes.py):

    A bare `create_deep_agent(model=<fake>, tools=[], response_format=X)`
    run was driven two ways:
      1. The fake model's final AIMessage carries ONLY `content` (a plain
         text "I'm done" style finalization, no tool call). Result:
         `structured_response` is None. The prose is silently discarded.
      2. The fake model's final AIMessage carries a `tool_calls` entry
         whose `name` equals the response schema's `__name__` (e.g.
         "RemediationOutcome") and whose `args` are a dict matching the
         schema's fields. Result: `structured_response` is a real,
         validated instance of the schema.

    Root cause (read from the installed `deepagents`/`langchain.agents`
    source, then confirmed by running both above): `create_deep_agent`
    forwards `response_format` into `langchain.agents.create_agent`, which
    -- absent a model with native "provider strategy" structured-output
    support (our fakes don't declare any) -- falls back to `ToolStrategy`:
    an artificial tool named after the schema class is added to the
    model's toolset, and the graph only ever populates
    `state["structured_response"]` from a tool call by that exact name
    (`langchain/agents/factory.py::_handle_model_output`). So every
    scripted "final answer" in this suite -- both the root's finalization
    (a plain `content=` AIMessage is fine there, since the root doesn't use
    response_format) and each nested worker's finalization (which DOES use
    `response_format=RemediationOutcome`) -- had to be built accordingly:
    the nested worker's last message is always a `tool_calls=[{"name":
    "RemediationOutcome", "args": {...}}]` AIMessage, never prose.

    A second, related finding while wiring the root<->nested boundary: the
    `remediate_target` subagent is a *custom* CompiledSubAgent (a raw
    StateGraph, not a `create_agent`-based one with its own
    `response_format`), so `deepagents`'s `_return_command_with_state_update`
    never finds a `structured_response` on its result and falls back to
    walking the subagent's own `messages` for the last non-empty AIMessage
    text -- but `subagent_wrapper._run` explicitly returns `"messages": []`
    on every path (there is no `add_messages` reducer on
    `_TargetSubagentState.messages`, so that return value REPLACES rather
    than appends). The upshot: the ToolMessage the root model sees back
    from a `task()` call to `remediate_target` is always the empty string.
    A real root LLM can still act on this because it also has the
    accumulated `remediations`/`requires_edges` STATE (not just messages)
    on subsequent turns in a real multi-turn agentic loop with tool
    introspection -- but our root fake model can only see `messages`, so
    the requires-signal test (test 2 below) scripts the root's dispatch of
    the companion dependency deterministically rather than pretending the
    fake model "read" the requires signal out of an empty ToolMessage. This
    mirrors the already-established pattern in
    `test_analysis_subgraph.py`'s `_CorrectiveRetryChatModel`, which routes
    off prior tool-call history, not tool-result content either.
"""

from __future__ import annotations

import re
import shutil
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from src.main_graph.subgraphs.remediation.deepagent import nodes as deepagent_nodes
from src.main_graph.subgraphs.remediation.deepagent import (
    subagent_wrapper as subagent_wrapper_module,
)
from src.main_graph.subgraphs.remediation.deepagent.state import (
    RemediationDeepAgentState,
)
from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
    build_target_subagent,
)
from src.main_graph.subgraphs.remediation.graph import build_remediation_subgraph
from src.main_graph.subgraphs.remediation.workspace import copy_repo as _real_copy_repo
from src.models.conductor import EvidenceRef, FindingNote
from src.models.results import AnalysisResult, PrepResult

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Network/subprocess containment (blanket, per task-10 brief) -- no test
# below exercises read_release_notes content, so this just returns a
# harmless "no releases" response if anything ever calls it.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_network_release_notes():
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"[]", b""))
    fake_proc.returncode = 0
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.tools.asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    ):
        yield


# ---------------------------------------------------------------------------
# Fake chat models
# ---------------------------------------------------------------------------


class _ContentRoutedChatModel(FakeMessagesListChatModel):
    """A FakeMessagesListChatModel whose bind_tools is a no-op (deepagents
    calls model.bind_tools(...) when building the agent; the stock
    implementation would wrap us in a RunnableBinding and break routing) and
    whose _generate is fully delegated to an injected `router` callable
    rather than a positional response list -- robust to however many model
    calls deepagents makes per turn/round (same rationale as
    test_analysis_subgraph.py's _CorrectiveRetryChatModel)."""

    responses: list[BaseMessage] = []
    router: Callable[[list[BaseMessage]], AIMessage] | None = None

    def bind_tools(
        self, tools: Sequence[Any], **kwargs: Any
    ) -> _ContentRoutedChatModel:
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[override]
        assert self.router is not None
        msg = self.router(list(messages))
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _msg_text(m: BaseMessage) -> str:
    content = getattr(m, "content", "")
    return content if isinstance(content, str) else str(content)


def _dispatched_deps(messages: list[BaseMessage]) -> set[str]:
    """Every dep name the root has already dispatched via task(), read off
    its own prior tool_calls (never off task()'s ToolMessage result, which
    is empty -- see the SPIKE note in the module docstring)."""
    deps: set[str] = set()
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls or []:
                if tc.get("name") == "task":
                    deps.add(tc["args"]["description"])
    return deps


def _task_call(dep: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {"description": dep, "subagent_type": "remediate_target"},
                "id": call_id,
            }
        ],
    )


def _multi_task_call(deps: list[str]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {"description": dep, "subagent_type": "remediate_target"},
                "id": f"call_{dep}",
            }
            for dep in deps
        ],
    )


def _root_router_sequential(deps_in_order: list[str]) -> Callable:
    """Dispatch each dep in `deps_in_order`, one task() call per model turn,
    then finalize. Content-routed off prior dispatches (see
    _dispatched_deps), so it behaves the same however many times deepagents
    calls the model, and (for the correction-round test) identically across
    fresh conversations in every retry round."""

    def _route(messages: list[BaseMessage]) -> AIMessage:
        dispatched = _dispatched_deps(messages)
        for i, dep in enumerate(deps_in_order):
            if dep not in dispatched:
                return _task_call(dep, f"call_{i}")
        return AIMessage(content="All targets dispatched, finalizing.")

    return _route


def _root_router_parallel(deps: list[str]) -> Callable:
    """Dispatch every dep in ONE AIMessage carrying multiple task()
    tool_calls (real GPT-5-class root models do this routinely for
    independent delegations), then finalize."""

    def _route(messages: list[BaseMessage]) -> AIMessage:
        dispatched = _dispatched_deps(messages)
        if not set(deps) <= dispatched:
            return _multi_task_call(deps)
        return AIMessage(content="All targets dispatched, finalizing.")

    return _route


_TARGET_RE = re.compile(r"dependency risk in a Node\.js project: ([\w@/.-]+)")


def _target_from_messages(messages: list[BaseMessage]) -> str:
    for m in messages:
        match = _TARGET_RE.search(_msg_text(m))
        if match:
            return match.group(1)
    msg = "could not find target dep in nested worker's own messages"
    raise AssertionError(msg)


def _outcome_call(call_id: str = "outcome", **kwargs: Any) -> AIMessage:
    """A finalizing AIMessage for a nested per-target worker: per the spike,
    structured_response is only populated when the tool call's name equals
    the response_format schema's __name__ ("RemediationOutcome")."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "RemediationOutcome", "args": kwargs, "id": call_id},
        ],
    )


def _immediate_outcome(**outcome_kwargs: Any) -> Callable:
    """A nested-worker route that finalizes immediately with a fixed
    RemediationOutcome, regardless of how many times it is called (the
    react loop only calls the model again if the prior turn wasn't a
    terminal structured-output tool call, so this is only ever invoked
    once per real target conversation)."""

    def _route(messages: list[BaseMessage]) -> AIMessage:
        return _outcome_call(**outcome_kwargs)

    return _route


def _read_then_write_then_outcome(
    notes_path: str, notes_content: str, **outcome_kwargs: Any
) -> Callable:
    """A nested-worker route that first reads a real file, then writes a
    real new file (both via deepagents' FilesystemMiddleware tools against
    the real FilesystemBackend(virtual_mode=True) clone), then finalizes --
    exercises concern 2 from the task-10 brief for real."""

    def _route(messages: list[BaseMessage]) -> AIMessage:
        ai_count = sum(1 for m in messages if isinstance(m, AIMessage))
        if ai_count == 0:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": "/package.json"},
                        "id": "read1",
                    }
                ],
            )
        if ai_count == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"file_path": notes_path, "content": notes_content},
                        "id": "write1",
                    }
                ],
            )
        return _outcome_call(**outcome_kwargs)

    return _route


def _worker_router_from(routes: dict[str, Callable]) -> Callable:
    def _route(messages: list[BaseMessage]) -> AIMessage:
        dep = _target_from_messages(messages)
        if dep not in routes:
            msg = f"no worker route registered for {dep!r}"
            raise AssertionError(msg)
        return routes[dep](messages)

    return _route


def _build_fake_root_agent(model: _ContentRoutedChatModel):
    """A real deep agent -- create_deep_agent with a fake root model but the
    REAL, unmodified remediate_target subagent (same construction as
    nodes._build_root_deep_agent, minus the real LLM)."""
    return create_deep_agent(
        model=model,
        tools=[],
        subagents=[build_target_subagent()],
        state_schema=RemediationDeepAgentState,
    )


async def _extract_target_dep_echo(description: str, known_targets: list[str]) -> str:
    """Replacement for subagent_wrapper._extract_target_dep: our root
    routers always set task()'s `description` to the literal dep name (see
    _task_call), so this is a trivial echo -- same pattern as
    test_analysis_subgraph.py's _extract_as."""
    return description


# ---------------------------------------------------------------------------
# Fixtures / seed data
# ---------------------------------------------------------------------------


def _write_repo(tmp_path: Path, direct_deps: dict[str, str]) -> str:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    package_json = {
        "name": "test-project",
        "version": "1.0.0",
        "dependencies": direct_deps,
    }
    import json

    (repo_dir / "package.json").write_text(json.dumps(package_json, indent=2))
    return str(repo_dir)


def _seed_prep(job_id: str, repo_path: str, direct_deps: dict[str, str]) -> PrepResult:
    return PrepResult(
        job_id=job_id,
        repo_path=repo_path,
        project_metadata={
            "name": "test-project",
            "package_manager": "npm",
            "direct_dependencies_count": len(direct_deps),
            "transitive_dependencies_count": 0,
        },
        manifest_files=["package.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": direct_deps, "packages": {}},
        discovery_summary="test-project dependency remediation fixture.",
        vector_store_id="",
    )


def _seed_analysis(job_id: str, findings: list[FindingNote]) -> AnalysisResult:
    return AnalysisResult(
        job_id=job_id,
        concern="dependency health",
        findings=findings,
        evidence_bundle_ids=[],
        iteration_count=1,
    )


def _finding(dep_name: str, description: str) -> FindingNote:
    return FindingNote(
        dep_name=dep_name,
        severity="critical",
        description=description,
        evidence=[EvidenceRef(tool="npm_audit", url=None, log_snippet="")],
    )


class _FakeGitPR:
    def __init__(self, pr_url: str = "https://github.com/acme/widgets/pull/1"):
        self.open_pr = AsyncMock(return_value=pr_url)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_pure_bump_target_ships_one_fixed_pr(
    tmp_path, result_dao, subgraph_config
):
    """Tier 0/1 regression: a single, uncoupled target with no requires
    signal verifies green and produces exactly one PR labeled "bump". Also
    exercises concern 2 (FilesystemBackend virtual_mode) for real: the
    nested worker reads package.json and writes a real new file into the
    real isolated copy_repo clone before finalizing."""
    job_id = f"rem-{uuid.uuid4().hex[:8]}"
    repo_path = _write_repo(tmp_path, {"leftpad": "1.0.0"})
    prep = _seed_prep(job_id, repo_path, {"leftpad": "1.0.0"})
    await result_dao.save_prep(prep)
    analysis = _seed_analysis(job_id, [_finding("leftpad", "leftpad has a known CVE")])
    await result_dao.save_analysis(analysis)

    fake_root_agent = _build_fake_root_agent(
        _ContentRoutedChatModel(router=_root_router_sequential(["leftpad"]))
    )
    worker_model = _ContentRoutedChatModel(
        router=_worker_router_from(
            {
                "leftpad": _read_then_write_then_outcome(
                    "/REMEDIATION_NOTES.md",
                    "Bumped leftpad to ^1.0.1\n",
                    strategy="bump",
                    to_range="^1.0.1",
                    summary="bumped leftpad",
                    status="fixed",
                )
            }
        )
    )

    captured_work_dirs: list[str] = []

    def _spy_copy_repo(src_repo_path: str) -> str:
        work_dir = _real_copy_repo(src_repo_path)
        captured_work_dirs.append(work_dir)
        return work_dir

    fake_git_pr = _FakeGitPR()
    subgraph_config["configurable"]["remediate"] = True
    subgraph_config["configurable"]["git_pr"] = fake_git_pr

    with (
        patch.object(deepagent_nodes, "_root_deep_agent", fake_root_agent),
        patch.object(
            subagent_wrapper_module,
            "_extract_target_dep",
            AsyncMock(side_effect=_extract_target_dep_echo),
        ),
        patch.object(
            subagent_wrapper_module, "get_llm", MagicMock(return_value=worker_model)
        ),
        patch.object(
            subagent_wrapper_module, "copy_repo", MagicMock(side_effect=_spy_copy_repo)
        ),
    ):
        graph = build_remediation_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "dependency health",
                "prep_result_id": prep.id,
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    try:
        assert result.get("remediation_result_id")
        remediation_result = await result_dao.get_remediation(
            result["remediation_result_id"]
        )
        assert len(remediation_result.remediations) == 1
        r = remediation_result.remediations[0]
        assert r.target_dep == "leftpad"
        assert r.status == "fixed"
        assert r.to_range == "^1.0.1"
        assert r.branch == f"remediation/{job_id[:8]}-leftpad"
        assert r.pr_url == "https://github.com/acme/widgets/pull/1"

        fake_git_pr.open_pr.assert_awaited_once()
        title = fake_git_pr.open_pr.await_args.args[2]
        assert "bump" in title

        # Concern 2: the nested worker's real read_file/write_file tool
        # calls actually touched a real file in the real isolated clone.
        assert len(captured_work_dirs) == 1
        notes_file = Path(captured_work_dirs[0]) / "REMEDIATION_NOTES.md"
        assert notes_file.exists()
        assert notes_file.read_text() == "Bumped leftpad to ^1.0.1\n"
    finally:
        for work_dir in captured_work_dirs:
            shutil.rmtree(Path(work_dir).parent, ignore_errors=True)


async def test_requires_signal_pulls_in_a_non_finding_companion(
    tmp_path, result_dao, subgraph_config
):
    """The eslint/eslint-plugin-react scenario from the spec: target A's
    subagent reports requires=["B"], B has no FindingNote, the root
    dispatches B, and both end up in ONE group/PR with B's
    Remediation.required_by == ["A"]."""
    job_id = f"rem-{uuid.uuid4().hex[:8]}"
    direct = {"eslint": "7.0.0", "eslint-plugin-react": "7.20.0"}
    repo_path = _write_repo(tmp_path, direct)
    prep = _seed_prep(job_id, repo_path, direct)
    await result_dao.save_prep(prep)
    # Only eslint has a finding -- eslint-plugin-react is pulled in purely
    # via the requires signal, never independently selected.
    analysis = _seed_analysis(job_id, [_finding("eslint", "eslint has a known CVE")])
    await result_dao.save_analysis(analysis)

    fake_root_agent = _build_fake_root_agent(
        _ContentRoutedChatModel(
            router=_root_router_sequential(["eslint", "eslint-plugin-react"])
        )
    )
    worker_model = _ContentRoutedChatModel(
        router=_worker_router_from(
            {
                "eslint": _immediate_outcome(
                    strategy="bump",
                    to_range="^8.0.0",
                    requires=["eslint-plugin-react"],
                    status="fixed",
                    summary="eslint bump requires plugin bump too",
                ),
                "eslint-plugin-react": _immediate_outcome(
                    strategy="bump",
                    to_range="^7.20.1",
                    status="fixed",
                    summary="companion bump for eslint-plugin-react",
                ),
            }
        )
    )

    fake_git_pr = _FakeGitPR()
    subgraph_config["configurable"]["remediate"] = True
    subgraph_config["configurable"]["git_pr"] = fake_git_pr

    with (
        patch.object(deepagent_nodes, "_root_deep_agent", fake_root_agent),
        patch.object(
            subagent_wrapper_module,
            "_extract_target_dep",
            AsyncMock(side_effect=_extract_target_dep_echo),
        ),
        patch.object(
            subagent_wrapper_module, "get_llm", MagicMock(return_value=worker_model)
        ),
    ):
        graph = build_remediation_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "dependency health",
                "prep_result_id": prep.id,
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("remediation_result_id")
    remediation_result = await result_dao.get_remediation(
        result["remediation_result_id"]
    )
    by_dep = {r.target_dep: r for r in remediation_result.remediations}
    assert set(by_dep) == {"eslint", "eslint-plugin-react"}
    assert by_dep["eslint"].status == "fixed"
    assert by_dep["eslint-plugin-react"].status == "fixed"
    assert by_dep["eslint-plugin-react"].required_by == ["eslint"]
    assert by_dep["eslint"].required_by == []

    # One shared PR for the whole connected group.
    fake_git_pr.open_pr.assert_awaited_once()
    title = fake_git_pr.open_pr.await_args.args[2]
    assert "eslint" in title
    assert "eslint-plugin-react" in title
    assert by_dep["eslint"].branch == by_dep["eslint-plugin-react"].branch
    assert by_dep["eslint"].pr_url == by_dep["eslint-plugin-react"].pr_url


async def test_parallel_task_calls_in_one_turn_do_not_crash_root_state(
    tmp_path, result_dao, subgraph_config
):
    """Drive the real graph with a fake root model that emits TWO tool
    calls to remediate_target in a single turn (two independent, uncoupled
    targets). Assert both targets' Remediation records land correctly in
    the final state -- proves RemediationDeepAgentState's reducers survive
    concurrent writes in one superstep, the exact bug class the
    analysis-subgraph swap hit and fixed (InvalidUpdateError: "Can receive
    only one value per step")."""
    job_id = f"rem-{uuid.uuid4().hex[:8]}"
    direct = {"pkg-a": "1.0.0", "pkg-b": "2.0.0"}
    repo_path = _write_repo(tmp_path, direct)
    prep = _seed_prep(job_id, repo_path, direct)
    await result_dao.save_prep(prep)
    analysis = _seed_analysis(
        job_id,
        [
            _finding("pkg-a", "pkg-a has a known CVE"),
            _finding("pkg-b", "pkg-b has a known CVE"),
        ],
    )
    await result_dao.save_analysis(analysis)

    fake_root_agent = _build_fake_root_agent(
        _ContentRoutedChatModel(router=_root_router_parallel(["pkg-a", "pkg-b"]))
    )
    worker_model = _ContentRoutedChatModel(
        router=_worker_router_from(
            {
                "pkg-a": _immediate_outcome(
                    strategy="bump", to_range="^1.0.1", status="fixed"
                ),
                "pkg-b": _immediate_outcome(
                    strategy="bump", to_range="^2.0.1", status="fixed"
                ),
            }
        )
    )

    with (
        patch.object(deepagent_nodes, "_root_deep_agent", fake_root_agent),
        patch.object(
            subagent_wrapper_module,
            "_extract_target_dep",
            AsyncMock(side_effect=_extract_target_dep_echo),
        ),
        patch.object(
            subagent_wrapper_module, "get_llm", MagicMock(return_value=worker_model)
        ),
    ):
        graph = build_remediation_subgraph()
        # The assertion under test is implicit: if the reducers were wrong
        # (plain LastValue channels instead of _keep_first_str/_merge_replace),
        # this ainvoke would raise InvalidUpdateError and the test would
        # fail here, before any of the state assertions below run.
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "dependency health",
                "prep_result_id": prep.id,
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("remediation_result_id")
    remediation_result = await result_dao.get_remediation(
        result["remediation_result_id"]
    )
    by_dep = {r.target_dep: r for r in remediation_result.remediations}
    assert set(by_dep) == {"pkg-a", "pkg-b"}
    assert by_dep["pkg-a"].status == "fixed"
    assert by_dep["pkg-a"].to_range == "^1.0.1"
    assert by_dep["pkg-b"].status == "fixed"
    assert by_dep["pkg-b"].to_range == "^2.0.1"


async def test_correction_round_retries_then_gives_up_at_cap(
    tmp_path, result_dao, subgraph_config
):
    """A target whose verification always fails: assert
    group_and_verify_gate retries it up to _MAX_CORRECTION_ROUNDS, then
    ships status="failed" with a reason, and that root_deepagent_node was
    invoked exactly (1 + _MAX_CORRECTION_ROUNDS) times, not more."""
    job_id = f"rem-{uuid.uuid4().hex[:8]}"
    direct = {"always-broken": "1.0.0"}
    repo_path = _write_repo(tmp_path, direct)
    prep = _seed_prep(job_id, repo_path, direct)
    await result_dao.save_prep(prep)
    analysis = _seed_analysis(
        job_id, [_finding("always-broken", "always-broken has a known CVE")]
    )
    await result_dao.save_analysis(analysis)

    # Every container.run call (npm_audit/npm_outdated during the initial
    # round, and verify_working_copy's install step during every group
    # verification) fails, so replay_and_verify_group can never go green.
    subgraph_config["configurable"]["container"].run = AsyncMock(
        return_value=(1, "", "npm install failed")
    )

    fake_root_agent = _build_fake_root_agent(
        _ContentRoutedChatModel(router=_root_router_sequential(["always-broken"]))
    )
    worker_model = _ContentRoutedChatModel(
        router=_worker_router_from(
            {
                "always-broken": _immediate_outcome(
                    strategy="bump", to_range="^2.0.0", status="fixed"
                )
            }
        )
    )

    call_count = 0

    async def _spy_root_node(state, config):
        # A plain async wrapper (rather than AsyncMock(wraps=...)) so
        # LangGraph's own arg-count introspection on the node callable still
        # sees a real (state, config) signature -- an AsyncMock wrapper
        # obscures that and LangGraph ends up invoking it with only `state`.
        nonlocal call_count
        call_count += 1
        return await deepagent_nodes.root_deepagent_node(state, config)

    with (
        patch.object(deepagent_nodes, "_root_deep_agent", fake_root_agent),
        patch.object(
            subagent_wrapper_module,
            "_extract_target_dep",
            AsyncMock(side_effect=_extract_target_dep_echo),
        ),
        patch.object(
            subagent_wrapper_module, "get_llm", MagicMock(return_value=worker_model)
        ),
        patch(
            "src.main_graph.subgraphs.remediation.graph.root_deepagent_node",
            _spy_root_node,
        ),
    ):
        graph = build_remediation_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "dependency health",
                "prep_result_id": prep.id,
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("remediation_result_id")
    remediation_result = await result_dao.get_remediation(
        result["remediation_result_id"]
    )
    assert len(remediation_result.remediations) == 1
    r = remediation_result.remediations[0]
    assert r.target_dep == "always-broken"
    assert r.status == "failed"
    assert r.skip_reason == "verification failed after max correction rounds"

    assert call_count == 1 + deepagent_nodes._MAX_CORRECTION_ROUNDS


async def test_consent_false_opens_zero_prs_across_every_group(
    tmp_path, result_dao, subgraph_config
):
    """Two independent fixed targets, remediate=False in configurable:
    assert the fake git_pr's open_pr is never called, and both
    Remediation records still have branch=None, pr_url=None, and a real
    (non-default) verification result -- proves group_and_verify_gate's
    deterministic verification runs unconditionally, and only
    pr_and_persist_node's PR step is gated on consent."""
    job_id = f"rem-{uuid.uuid4().hex[:8]}"
    direct = {"pkg-c": "1.0.0", "pkg-d": "2.0.0"}
    repo_path = _write_repo(tmp_path, direct)
    prep = _seed_prep(job_id, repo_path, direct)
    await result_dao.save_prep(prep)
    analysis = _seed_analysis(
        job_id,
        [
            _finding("pkg-c", "pkg-c has a known CVE"),
            _finding("pkg-d", "pkg-d has a known CVE"),
        ],
    )
    await result_dao.save_analysis(analysis)

    fake_root_agent = _build_fake_root_agent(
        _ContentRoutedChatModel(router=_root_router_parallel(["pkg-c", "pkg-d"]))
    )
    worker_model = _ContentRoutedChatModel(
        router=_worker_router_from(
            {
                "pkg-c": _immediate_outcome(
                    strategy="bump", to_range="^1.0.1", status="fixed"
                ),
                "pkg-d": _immediate_outcome(
                    strategy="bump", to_range="^2.0.1", status="fixed"
                ),
            }
        )
    )

    fake_git_pr = _FakeGitPR()
    subgraph_config["configurable"]["remediate"] = False
    subgraph_config["configurable"]["git_pr"] = fake_git_pr

    with (
        patch.object(deepagent_nodes, "_root_deep_agent", fake_root_agent),
        patch.object(
            subagent_wrapper_module,
            "_extract_target_dep",
            AsyncMock(side_effect=_extract_target_dep_echo),
        ),
        patch.object(
            subagent_wrapper_module, "get_llm", MagicMock(return_value=worker_model)
        ),
    ):
        graph = build_remediation_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "dependency health",
                "prep_result_id": prep.id,
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("remediation_result_id")
    remediation_result = await result_dao.get_remediation(
        result["remediation_result_id"]
    )
    by_dep = {r.target_dep: r for r in remediation_result.remediations}
    assert set(by_dep) == {"pkg-c", "pkg-d"}
    for r in by_dep.values():
        assert r.status == "fixed"
        assert r.branch is None
        assert r.pr_url is None
        assert r.verification.installed is True

    fake_git_pr.open_pr.assert_not_called()

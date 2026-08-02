"""
Blackbox integration test for the analysis subgraph (deepagent-based).

What is real:
- LangGraph wiring: analysis_deepagent_node -> coverage_gate ->
  (loop | backstop_dispatch | save_analysis_result)
- save_analysis_result (MongoDB persistence via testcontainer)
- AnalysisState accumulation (bundle_ids, agent_calls) via reducers, INCLUDING
  the delta-slicing in analysis_deepagent_node that must not double-count
  bundle_ids/agent_calls across corrective retry rounds
- Every domain-agent's actual run() logic (only each agent's underlying LLM
  call is mocked, same as before) and the deterministic coverage backstop

What is mocked (three LLM call sites now, up from two before the swap -- this
is a real, structural cost of the deepagent swap, not an oversight):
- The deep agent's own root model (scripted task() tool calls, via a
  FakeMessagesListChatModel)
- subagent_wrapper._extract_dispatch (extracts a typed AgentDispatch from the
  root's free-text task() description)
- base_agent._llm (returns a canned DomainAgentDecision, same as before)
- vulnerability_agent.trivy_vuln_scan (the vulnerability agent is deterministic: it
  runs Trivy, not an LLM)

Two behaviours the task-6 brief guessed wrong; corrected here to match what the
real graph actually does (see task-6-report.md for the full write-up):

1. `vulnerability_agent` is a WHOLE-TREE agent (coverage.WHOLE_TREE_AGENT_TYPES)
   and never counts toward per-direct-dependency coverage. So delegating ONLY
   to it always leaves every direct dep "missing", which forces the correction
   loop and, after the retry budget, the deterministic backstop. A clean
   "one delegation -> save, no backstop" happy path therefore has to delegate
   to a PACKAGE-SCOPED agent (test 1 uses maintenance_agent).

2. The backstop's default agent is `web_research_agent`
   (backstop._DEFAULT_AGENT_TYPE), NOT vulnerability_agent, and it runs the
   normal base_agent react loop -- so the backstop path DOES need base_agent._llm
   mocked (test 2 corrects the brief's "zero extra LLM mocking" claim).

Multiple task() tool_calls packed into a SINGLE scripted AIMessage both
execute (deepagents runs each subagent): every CompiledSubAgent echoes
job_id/prep_result_id back through a Command(update=...), and those two keys
carry an Annotated[str, _keep_first] reducer on AnalysisDeepAgentState (see
deepagent/state.py) specifically so two concurrent identical writes in one
superstep do not raise InvalidUpdateError (test
`test_parallel_task_calls_in_one_turn_do_not_crash_root_state` below is the
regression test for that). The realistic multi-delegation form across
correction rounds is still sequential task() calls (test 3 spreads them
across two correction rounds, which also exercises the delta-slicing on a
genuine second round). See task-6-report.md.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from src.main_graph.subgraphs.analysis.concern import Concern
from src.main_graph.subgraphs.analysis.deepagent import nodes as deepagent_nodes
from src.main_graph.subgraphs.analysis.graph import build_analysis_subgraph
from src.models.conductor import EvidenceRef, FindingNote
from src.models.results import AgentDispatch, DomainAgentDecision, PrepResult


class _ScriptedToolCallingChatModel(FakeMessagesListChatModel):
    """A FakeMessagesListChatModel whose bind_tools is a no-op.

    deepagents calls model.bind_tools(...) when it builds the root agent; the
    default FakeMessagesListChatModel.bind_tools would wrap the model in a
    RunnableBinding that no longer cycles our scripted `responses`, so we
    override it to return self unchanged (mirrors
    tests/unit/subgraphs/analysis/deepagent/test_state_passthrough.py)."""

    def bind_tools(
        self, tools: Sequence[Any], **kwargs: Any
    ) -> _ScriptedToolCallingChatModel:
        return self


class _CorrectiveRetryChatModel(FakeMessagesListChatModel):
    """A root model that routes on conversation CONTENT, not a positional
    counter.

    Scripting a multi-round deep-agent run with a plain
    FakeMessagesListChatModel is unreliable: deepagents may make a
    non-obvious number of model calls per _deep_agent.ainvoke(), so a global
    response counter desyncs across correction rounds (and can accidentally
    re-dispatch an agent). This model decides purely from the messages it is
    handed, so it behaves identically no matter how many times it is called:

    - round 1 (no vulnerability_agent delegated yet): delegate to
      vulnerability_agent (a whole-tree agent -> does NOT satisfy per-dep
      coverage), then finalize.
    - round 2 (coverage_gate injected its "still need coverage" prompt):
      delegate to maintenance_agent (package-scoped -> covers lodash), then
      finalize.

    That is exactly one genuine correction round driven by a real coverage gap.
    """

    responses: list[BaseMessage] = []  # unused; _generate is content-routed

    def bind_tools(
        self, tools: Sequence[Any], **kwargs: Any
    ) -> _CorrectiveRetryChatModel:
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[override]
        delegated = {
            tc["args"].get("subagent_type")
            for m in messages
            if isinstance(m, AIMessage)
            for tc in (m.tool_calls or [])
            if tc.get("name") == "task"
        }
        has_correction_prompt = any(
            isinstance(m, HumanMessage) and "still need coverage" in str(m.content)
            for m in messages
        )
        if "vulnerability_agent" not in delegated:
            msg: BaseMessage = _task_call(
                "Check lodash for CVEs.", "vulnerability_agent", "call_vuln"
            )
        elif has_correction_prompt and "maintenance_agent" not in delegated:
            msg = _task_call(
                "Check whether lodash is maintained.", "maintenance_agent", "call_maint"
            )
        else:
            msg = AIMessage(content="Finalizing.")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _seed_prep(job_id: str) -> PrepResult:
    return PrepResult(
        job_id=job_id,
        repo_path="/tmp/test-repo",
        project_metadata={
            "name": "test-project",
            "package_manager": "npm",
            "direct_dependencies_count": 1,
            "transitive_dependencies_count": 0,
        },
        manifest_files=["package.json", "package-lock.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": {"lodash": "4.17.20"}, "packages": {}},
    )


# vulnerability_agent is deterministic: it runs Trivy, not an LLM, and
# extracts every advisory. Feed it a canned Trivy scan result so the graph
# wiring can be exercised without a real repo -- same fixture the old test used.
_TRIVY_FIXTURE = {
    "SchemaVersion": 2,
    "Results": [
        {
            "Vulnerabilities": [
                {
                    "VulnerabilityID": "CVE-2021-23337",
                    "PkgName": "lodash",
                    "InstalledVersion": "4.17.20",
                    "FixedVersion": "4.17.21",
                    "Severity": "HIGH",
                    "Title": "prototype pollution in lodash < 4.17.21",
                    "Description": "Lodash prototype pollution vulnerability",
                    "PrimaryURL": "https://nvd.nist.gov/vuln/detail/CVE-2021-23337",
                }
            ]
        }
    ],
}


def _task_call(description: str, subagent_type: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {"description": description, "subagent_type": subagent_type},
                "id": call_id,
            }
        ],
    )


def _multi_task_call(
    calls: list[tuple[str, str, str]],
) -> AIMessage:
    """One AIMessage carrying MULTIPLE task() tool_calls -- the shape a real
    GPT-5-class root model routinely emits when delegating to several
    specialists in one turn."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {"description": description, "subagent_type": subagent_type},
                "id": call_id,
            }
            for description, subagent_type, call_id in calls
        ],
    )


def _build_deep_agent_with_model(model):
    """Build a real deep agent around a given (fake) root model.

    We construct the agent directly with create_deep_agent (rather than
    patching nodes.get_llm and calling nodes._build_deep_agent()) so there is no
    patch-ordering trap: the fake model is baked in before the agent is
    compiled. The resulting agent is a real deepagents graph -- only the root
    LLM is fake."""
    subagents = [
        deepagent_nodes.build_agent_subagent(agent_type)
        for agent_type in deepagent_nodes.REGISTRY
    ]
    from deepagents import create_deep_agent

    return create_deep_agent(
        model=model,
        subagents=subagents,
        state_schema=deepagent_nodes.AnalysisDeepAgentState,
    )


def _build_fake_deep_agent(root_responses: list[AIMessage]):
    """Build a real deep agent whose root model replays `root_responses`."""
    return _build_deep_agent_with_model(
        _ScriptedToolCallingChatModel(responses=root_responses)
    )


async def _extract_as(description: str, agent_type: str) -> AgentDispatch:
    """Stand-in for subagent_wrapper._extract_dispatch: echo the requested
    agent_type and always focus lodash (the single direct dep), so a
    package-scoped delegation actually counts as covering lodash."""
    return AgentDispatch(
        domain=agent_type,
        hypothesis=description,
        packages_to_focus=["lodash"],
        agent_type=agent_type,
    )


def _fake_concern_llm(concern: Concern) -> MagicMock:
    """Mock understand_concern's _llm: with_structured_output(...).ainvoke(...) ->
    the given Concern, every call."""
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=concern
    )
    return mock_llm


def _fake_base_llm(decision: DomainAgentDecision) -> MagicMock:
    """Mock base_agent._llm: _llm.with_structured_output(...).ainvoke(...) ->
    the given DomainAgentDecision, every call."""
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=decision)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=chain)
    return llm


def _fake_base_llm_by_hypothesis(
    routes: dict[str, DomainAgentDecision],
) -> MagicMock:
    """Mock base_agent._llm that picks a DomainAgentDecision by matching a
    substring against the system message content (which _react_loop
    interpolates dispatch.hypothesis into). Lets two concurrently-dispatched
    package-scoped agents in the same test return genuinely distinct
    findings, rather than the byte-identical findings dedup_findings would
    (correctly) collapse."""

    async def _ainvoke(messages: list[dict]) -> DomainAgentDecision:
        system_content = str(messages[0]["content"])
        for needle, decision in routes.items():
            if needle in system_content:
                return decision
        msg = f"no route matched system content: {system_content!r}"
        raise AssertionError(msg)

    chain = MagicMock()
    chain.ainvoke = AsyncMock(side_effect=_ainvoke)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=chain)
    return llm


@pytest.mark.asyncio
async def test_analysis_dispatches_agent_and_saves_result(subgraph_config, result_dao):
    """Happy path: the root deep agent delegates once to a package-scoped agent
    (maintenance_agent) that covers the sole direct dep, coverage_gate is
    satisfied, and the AnalysisResult lands in MongoDB -- no correction loop, no
    backstop. Exercises all three LLM call sites of the swapped design."""
    job_id = f"anal-{uuid.uuid4().hex[:8]}"
    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    fake_deep_agent = _build_fake_deep_agent(
        [
            _task_call(
                "Check whether lodash@4.17.20 is still maintained.",
                "maintenance_agent",
                "call_1",
            ),
            AIMessage(content="Sufficient evidence collected, finalizing."),
        ]
    )

    decision = DomainAgentDecision(
        tool_calls=[],
        findings=[
            FindingNote(
                dep_name="lodash",
                severity="high",
                description="lodash 4.17.20 is behind on maintenance",
                evidence=[EvidenceRef(tool="npm_outdated", url=None, log_snippet="")],
            )
        ],
        summary="lodash is behind",
        confidence=0.9,
        finalize=True,
        reasoning="done",
    )

    with (
        patch.object(deepagent_nodes, "_deep_agent", fake_deep_agent),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper._extract_dispatch",
            new=AsyncMock(side_effect=_extract_as),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.base_agent._llm",
            _fake_base_llm(decision),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _fake_concern_llm(
                Concern(
                    is_valid=True,
                    type=["maintenance"],
                    scope="all_dependencies",
                    packages=[],
                    requires_per_dependency_analysis=True,
                    preferred_agents=["maintenance_agent"],
                )
            ),
        ),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "dependency health",
                "prep_result_id": prep.id,
                "bundle_ids": [],
                "agent_calls": [],
            },
            config=subgraph_config,
        )

    assert result.get("analysis_result_id")
    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    assert analysis.job_id == job_id
    assert len(analysis.findings) == 1
    assert analysis.findings[0].dep_name == "lodash"
    assert analysis.findings[0].severity == "high"
    assert len(analysis.evidence_bundle_ids) == 1

    job_repo = subgraph_config["configurable"]["job_repo"]
    job_repo.update_artifact_data.assert_awaited_once()
    call = job_repo.update_artifact_data.await_args
    assert call.args[0] == job_id
    assert call.args[1] == "analysis"
    agent_calls = call.args[2]["agent_calls"]
    assert len(agent_calls) == 1
    assert agent_calls[0]["agent_type"] == "maintenance_agent"


@pytest.mark.asyncio
async def test_backstop_fires_when_deep_agent_never_delegates(
    subgraph_config, result_dao
):
    """Root deep agent finalizes immediately without ever calling task().

    coverage_gate then loops the deep agent through its full correction budget
    (each retry a no-op that must NOT accumulate any phantom bundles/agent_calls
    -- a direct check on the delta-slicing across empty rounds), and after
    _MAX_CORRECTION_ROUNDS routes to backstop_dispatch. The backstop
    deterministically dispatches its default agent (web_research_agent) for the
    still-uncovered lodash, so lodash is covered in the end. Because the backstop
    runs the normal base_agent react loop, base_agent._llm is mocked here."""
    job_id = f"anal-{uuid.uuid4().hex[:8]}"
    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    fake_deep_agent = _build_fake_deep_agent(
        [AIMessage(content="Nothing to check here, finalizing.")]
    )

    decision = DomainAgentDecision(
        tool_calls=[],
        findings=[
            FindingNote(
                dep_name="lodash",
                severity="high",
                description="Backstop-surfaced concern for lodash",
                evidence=[EvidenceRef(tool="web_search", url=None, log_snippet="")],
            )
        ],
        summary="lodash flagged by backstop",
        confidence=0.7,
        finalize=True,
        reasoning="done",
    )

    with (
        patch.object(deepagent_nodes, "_deep_agent", fake_deep_agent),
        patch(
            "src.main_graph.subgraphs.analysis.agents.base_agent._llm",
            _fake_base_llm(decision),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _fake_concern_llm(
                Concern(
                    is_valid=True,
                    type=["maintenance"],
                    scope="all_dependencies",
                    packages=[],
                    requires_per_dependency_analysis=True,
                    preferred_agents=["maintenance_agent"],
                )
            ),
        ),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "unmaintained dependencies",
                "prep_result_id": prep.id,
                "bundle_ids": [],
                "agent_calls": [],
            },
            config=subgraph_config,
        )

    assert result.get("analysis_result_id")
    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    # The deep agent never delegated, but the deterministic backstop covered
    # lodash -- exactly one bundle/one finding, proving the empty correction
    # rounds accumulated no phantom bundles.
    assert len(analysis.findings) == 1
    assert analysis.findings[0].dep_name == "lodash"
    assert len(analysis.evidence_bundle_ids) == 1

    job_repo = subgraph_config["configurable"]["job_repo"]
    job_repo.update_artifact_data.assert_awaited_once()
    call = job_repo.update_artifact_data.await_args
    agent_calls = call.args[2]["agent_calls"]
    assert len(agent_calls) == 1
    # The backstop's default agent is web_research_agent, not vulnerability_agent.
    assert agent_calls[0]["agent_type"] == "web_research_agent"


@pytest.mark.asyncio
async def test_analysis_accumulates_bundles_across_two_correction_rounds(
    subgraph_config, result_dao
):
    """A genuine second correction round, driven by a REAL coverage gap -- the
    first running end-to-end proof of analysis_deepagent_node's delta-slicing.

    Round 1: the root deep agent delegates only to vulnerability_agent (a
    whole-tree agent that runs Trivy). It surfaces the lodash CVE, but
    whole-tree agents never count toward per-dep coverage, so coverage_gate
    finds lodash still uncovered and loops the deep agent.
    Round 2: with coverage_gate's "still need coverage" prompt in context, the
    deep agent delegates to maintenance_agent (package-scoped) which covers
    lodash -> coverage satisfied -> save. No backstop.

    The final AnalysisResult must hold exactly two DISTINCT bundles and two
    findings. If the delta-slicing were wrong (e.g. round 2 re-emitted round 1's
    already-accumulated bundle_ids), the vulnerability bundle would appear twice
    -- this test fails loudly on that."""
    job_id = f"anal-{uuid.uuid4().hex[:8]}"
    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    # Content-routed root model: vulnerability_agent in round 1, maintenance_agent
    # in round 2 (once coverage_gate injects its correction prompt). Robust to
    # however many model calls deepagents makes per ainvoke.
    fake_deep_agent = _build_deep_agent_with_model(_CorrectiveRetryChatModel())

    maintenance_decision = DomainAgentDecision(
        tool_calls=[],
        findings=[
            FindingNote(
                dep_name="lodash",
                severity="medium",
                description="Finding from maintenance agent",
                evidence=[EvidenceRef(tool="npm_outdated", url=None, log_snippet="")],
            )
        ],
        summary="One finding found",
        confidence=0.8,
        finalize=True,
        reasoning="done",
    )

    with (
        patch.object(deepagent_nodes, "_deep_agent", fake_deep_agent),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper._extract_dispatch",
            new=AsyncMock(side_effect=_extract_as),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent.trivy_vuln_scan",
            AsyncMock(return_value=_TRIVY_FIXTURE),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.base_agent._llm",
            _fake_base_llm(maintenance_decision),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.nodes.whole_tree_scan_satisfies_concern",
            AsyncMock(return_value=False),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _fake_concern_llm(
                Concern(
                    is_valid=True,
                    type=["maintenance"],
                    scope="all_dependencies",
                    packages=[],
                    requires_per_dependency_analysis=True,
                    preferred_agents=["maintenance_agent"],
                )
            ),
        ),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "dependency health",
                "prep_result_id": prep.id,
                "bundle_ids": [],
                "agent_calls": [],
            },
            config=subgraph_config,
        )

    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    # Exactly two distinct bundles / two findings -- no duplication from the
    # second correction round.
    assert len(analysis.evidence_bundle_ids) == 2
    assert len(set(analysis.evidence_bundle_ids)) == 2
    assert len(analysis.findings) == 2

    job_repo = subgraph_config["configurable"]["job_repo"]
    call = job_repo.update_artifact_data.await_args
    agent_calls = call.args[2]["agent_calls"]
    assert len(agent_calls) == 2
    assert {c["agent_type"] for c in agent_calls} == {
        "vulnerability_agent",
        "maintenance_agent",
    }


@pytest.mark.asyncio
async def test_parallel_task_calls_in_one_turn_do_not_crash_root_state(
    subgraph_config, result_dao
):
    """Regression test for the final-review Finding 1 crash: the root deep
    agent's LLM emits TWO task() tool_calls packed into a SINGLE AIMessage
    (real GPT-5-class models do this routinely for independent delegations),
    dispatching to two DIFFERENT package-scoped specialists
    (maintenance_agent, supply_chain_agent) in the same root turn.

    Both CompiledSubAgent runnables echo job_id/prep_result_id back to the
    root via Command(update=...) in the SAME superstep. Before the fix,
    AnalysisDeepAgentState declared job_id/prep_result_id as plain (LastValue)
    channels, so this raised
    `InvalidUpdateError: Can receive only one value per step` and crashed the
    whole job. After the fix (Annotated[str, _keep_first] reducer in
    deepagent/state.py) both writes are tolerated because the value is
    invariant across the run, and the graph completes normally with both
    subagents' findings persisted."""
    job_id = f"anal-{uuid.uuid4().hex[:8]}"
    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    fake_deep_agent = _build_fake_deep_agent(
        [
            _multi_task_call(
                [
                    (
                        "Check whether lodash@4.17.20 is still maintained.",
                        "maintenance_agent",
                        "call_maint",
                    ),
                    (
                        "Check lodash for typosquatting / supply-chain risk.",
                        "supply_chain_agent",
                        "call_supply",
                    ),
                ]
            ),
            AIMessage(content="Sufficient evidence collected, finalizing."),
        ]
    )

    maintenance_decision = DomainAgentDecision(
        tool_calls=[],
        findings=[
            FindingNote(
                dep_name="lodash",
                severity="medium",
                description="lodash maintenance finding",
                evidence=[EvidenceRef(tool="npm_outdated", url=None, log_snippet="")],
            )
        ],
        summary="maintenance finding",
        confidence=0.8,
        finalize=True,
        reasoning="done",
    )
    supply_chain_decision = DomainAgentDecision(
        tool_calls=[],
        findings=[
            FindingNote(
                dep_name="lodash",
                severity="medium",
                description="lodash supply-chain finding",
                evidence=[
                    EvidenceRef(tool="typosquat_detection", url=None, log_snippet="")
                ],
            )
        ],
        summary="supply-chain finding",
        confidence=0.8,
        finalize=True,
        reasoning="done",
    )

    with (
        patch.object(deepagent_nodes, "_deep_agent", fake_deep_agent),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper._extract_dispatch",
            new=AsyncMock(side_effect=_extract_as),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.base_agent._llm",
            _fake_base_llm_by_hypothesis(
                {
                    "still maintained": maintenance_decision,
                    "typosquatting": supply_chain_decision,
                }
            ),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _fake_concern_llm(
                Concern(
                    is_valid=True,
                    type=["maintenance", "supply_chain"],
                    scope="all_dependencies",
                    packages=[],
                    requires_per_dependency_analysis=True,
                    preferred_agents=["maintenance_agent", "supply_chain_agent"],
                )
            ),
        ),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "dependency health",
                "prep_result_id": prep.id,
                "bundle_ids": [],
                "agent_calls": [],
            },
            config=subgraph_config,
        )

    assert result.get("analysis_result_id")
    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    assert len(analysis.evidence_bundle_ids) == 2
    assert len(set(analysis.evidence_bundle_ids)) == 2
    assert len(analysis.findings) == 2
    assert {f.description for f in analysis.findings} == {
        "lodash maintenance finding",
        "lodash supply-chain finding",
    }

    job_repo = subgraph_config["configurable"]["job_repo"]
    call = job_repo.update_artifact_data.await_args
    agent_calls = call.args[2]["agent_calls"]
    assert len(agent_calls) == 2
    assert {c["agent_type"] for c in agent_calls} == {
        "maintenance_agent",
        "supply_chain_agent",
    }


@pytest.mark.asyncio
async def test_coverage_gate_skips_per_package_coverage_when_whole_tree_scan_satisfies_concern(  # noqa: E501
    subgraph_config, result_dao
):
    """Regression test for the redundant web_research_agent dispatch found in
    job 6a6db91f414c989f5ecd71a9: concern is purely about known
    vulnerabilities, vulnerability_agent's Trivy scan succeeds, and the
    coverage judge says that fully addresses the concern. coverage_gate must
    then short-circuit missing_deps to [] -- no correction-round loop-back,
    no backstop_dispatch, no web_research_agent/maintenance_agent/
    supply_chain_agent ever dispatched."""
    job_id = f"anal-{uuid.uuid4().hex[:8]}"
    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    fake_deep_agent = _build_fake_deep_agent(
        [
            _task_call(
                "Scan the whole dependency tree for known CVEs.",
                "vulnerability_agent",
                "call_vuln",
            ),
            AIMessage(content="Sufficient evidence collected, finalizing."),
        ]
    )

    with (
        patch.object(deepagent_nodes, "_deep_agent", fake_deep_agent),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper._extract_dispatch",
            new=AsyncMock(side_effect=_extract_as),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent.trivy_vuln_scan",
            AsyncMock(return_value=_TRIVY_FIXTURE),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.nodes.whole_tree_scan_satisfies_concern",
            AsyncMock(return_value=True),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _fake_concern_llm(
                Concern(
                    is_valid=True,
                    type=["vulnerability"],
                    scope="all_dependencies",
                    packages=[],
                    requires_per_dependency_analysis=True,
                    preferred_agents=["vulnerability_agent"],
                )
            ),
        ),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "analyze vulnerable dependencies",
                "prep_result_id": prep.id,
                "bundle_ids": [],
                "agent_calls": [],
            },
            config=subgraph_config,
        )

    assert result.get("analysis_result_id")
    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    assert len(analysis.evidence_bundle_ids) == 1
    assert len(analysis.findings) == 1
    assert analysis.findings[0].dep_name == "lodash"

    job_repo = subgraph_config["configurable"]["job_repo"]
    call = job_repo.update_artifact_data.await_args
    agent_calls = call.args[2]["agent_calls"]
    # Only vulnerability_agent ran -- the coverage judge prevented a forced
    # dispatch of web_research_agent (or any other package-scoped agent) for
    # a concern the Trivy scan already fully answered.
    assert len(agent_calls) == 1
    assert agent_calls[0]["agent_type"] == "vulnerability_agent"

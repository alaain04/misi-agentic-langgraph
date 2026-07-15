from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.models.results import AgentDispatch, EvidenceBundle, PrepResult, DomainAgentDecision
from src.models.conductor import FindingNote


def _prep() -> PrepResult:
    return PrepResult(
        job_id="j1", repo_path="/tmp/r", project_metadata={},
        manifest_files=[], detected_package_manager="npm",
        dependency_graph={},
        discovery_summary="s", vector_store_id="vs1",
    )


def _dispatch(agent_type: str = "vulnerability_agent") -> AgentDispatch:
    return AgentDispatch(
        domain="vulnerabilities", hypothesis="check CVEs",
        packages_to_focus=["express"], agent_type=agent_type,
    )


@pytest.mark.asyncio
async def test_vulnerability_agent_run_extracts_all_audit_findings():
    """run() audits the whole tree deterministically and takes every finding at
    or above the configured severity — no LLM, no package sampling."""
    from src.main_graph.subgraphs.analysis.agents import vulnerability_agent

    audit_output = {
        "advisories": {
            "1": {"module_name": "lodash", "severity": "high", "title": "Code injection",
                  "vulnerable_versions": "<=4.17.23", "patched_versions": "<0.0.0",
                  "cves": ["CVE-1"], "url": "https://x/1", "findings": [{"version": "4.17.21"}]},
            "2": {"module_name": "form-data", "severity": "critical", "title": "Unsafe random",
                  "vulnerable_versions": "<2.5.4", "patched_versions": ">=2.5.4",
                  "cves": [], "url": "https://x/2", "findings": [{"version": "2.5.0"}]},
        }
    }
    audit = AsyncMock(return_value=audit_output)

    with patch.object(vulnerability_agent, "npm_audit", audit), \
         patch.object(vulnerability_agent.settings, "vuln_min_severity", "high"):
        bundle, tools_used, react_iterations = await vulnerability_agent.VulnerabilityAgent().run(_dispatch(), _prep())

    assert isinstance(bundle, EvidenceBundle)
    assert tools_used == ["npm_audit"]
    assert react_iterations == 1
    assert bundle.confidence == 1.0
    assert bundle.packages_to_focus == []  # not sampled — whole tree
    names = {f.dep_name for f in bundle.findings}
    assert names == {"lodash", "form-data"}
    assert bundle.findings[0].severity == "critical"  # sorted most-severe first


@pytest.mark.asyncio
async def test_agent_run_accepts_bare_async_functions():
    """Bare async functions (no .name attr) must not crash the react loop."""
    from src.main_graph.subgraphs.analysis.agents.base_agent import _react_loop

    async def npm_audit(repo_path: str) -> dict:
        return {"vulnerabilities": []}

    final_decision = DomainAgentDecision(
        tool_calls=[], findings=[],
        summary="No issues", confidence=0.8, finalize=True, reasoning="done",
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=final_decision)

    with patch("src.main_graph.subgraphs.analysis.agents.base_agent._llm", mock_llm):
        bundle, tools_used, react_iterations = await _react_loop(_dispatch(), _prep(), [npm_audit], "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}")

    assert isinstance(bundle, EvidenceBundle)
    assert bundle.domain == "vulnerabilities"
    assert tools_used == []
    assert react_iterations == 1


def test_registry_has_expected_agents():
    from src.main_graph.subgraphs.analysis.utils.registry import REGISTRY
    assert "vulnerability_agent" in REGISTRY
    assert "maintenance_agent" in REGISTRY
    assert "supply_chain_agent" in REGISTRY
    assert "web_research_agent" in REGISTRY


def test_agent_get_tools_returns_list():
    from src.main_graph.subgraphs.analysis.agents.vulnerability_agent import VulnerabilityAgent
    tools = VulnerabilityAgent().get_tools(_prep())
    assert isinstance(tools, list)
    assert len(tools) > 0


def _finalize_decision(findings, confidence=0.9):
    return DomainAgentDecision(
        tool_calls=[], findings=findings,
        summary="draft", confidence=confidence, finalize=True, reasoning="done",
    )


@pytest.mark.asyncio
async def test_react_loop_self_corrects_then_passes():
    from src.main_graph.subgraphs.analysis.agents import base_agent
    from src.main_graph.subgraphs.analysis.agents.critique_agent import FindingsVerdict

    finding = FindingNote(dep_name="express", severity="high", description="CVE", evidence=[])
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize_decision([finding])
    )
    critic = AsyncMock(side_effect=[
        FindingsVerdict(ok=False, feedback="add evidence for express", calibrated_confidence=0.2),
        FindingsVerdict(ok=True, feedback="", calibrated_confidence=0.85),
    ])

    with patch.object(base_agent, "_llm", mock_llm), \
         patch.object(base_agent, "critique_findings", critic):
        bundle, tools_used, react_iterations = await base_agent._react_loop(
            _dispatch(), _prep(), [],
            "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}",
        )

    assert bundle.confidence == 0.85
    assert bundle.verification_note is None
    assert tools_used == ["verification_feedback"]
    assert react_iterations == 2
    assert critic.await_count == 2  # rejected once, re-verified after self-correction
    assert mock_llm.with_structured_output.return_value.ainvoke.await_count == 2


@pytest.mark.asyncio
async def test_react_loop_attaches_note_when_budget_exhausted():
    from src.main_graph.subgraphs.analysis.agents import base_agent
    from src.main_graph.subgraphs.analysis.agents.critique_agent import FindingsVerdict

    finding = FindingNote(dep_name="express", severity="high", description="CVE", evidence=[])
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize_decision([finding])
    )
    critic = AsyncMock(return_value=FindingsVerdict(
        ok=False, feedback="express finding unsupported", calibrated_confidence=0.1,
    ))

    with patch.object(base_agent, "_llm", mock_llm), \
         patch.object(base_agent, "_MAX_ITERATIONS", 2), \
         patch.object(base_agent, "critique_findings", critic):
        bundle, tools_used, react_iterations = await base_agent._react_loop(
            _dispatch(), _prep(), [],
            "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}",
        )

    assert bundle.findings == [finding]  # kept, not pruned
    assert bundle.confidence == 0.1
    assert bundle.verification_note == "express finding unsupported"
    assert tools_used == ["verification_feedback"]
    assert react_iterations == 2


@pytest.mark.asyncio
async def test_react_loop_critic_failure_degrades_to_pass():
    from src.main_graph.subgraphs.analysis.agents import base_agent

    finding = FindingNote(dep_name="express", severity="high", description="CVE", evidence=[])
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize_decision([finding], confidence=0.9)
    )
    critic = AsyncMock(side_effect=RuntimeError("critic down"))

    with patch.object(base_agent, "_llm", mock_llm), \
         patch.object(base_agent, "critique_findings", critic):
        bundle, tools_used, react_iterations = await base_agent._react_loop(
            _dispatch(), _prep(), [],
            "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}",
        )

    assert bundle.confidence == 0.9
    assert bundle.verification_note is None
    assert tools_used == []
    assert react_iterations == 1


@pytest.mark.asyncio
async def test_react_loop_survives_malformed_decision_then_recovers():
    """A malformed structured-output response (e.g. LLM omits required fields)
    must not crash the loop; it should retry the next iteration."""
    from src.main_graph.subgraphs.analysis.agents import base_agent
    from src.main_graph.subgraphs.analysis.agents.critique_agent import FindingsVerdict

    finding = FindingNote(dep_name="express", severity="high", description="CVE", evidence=[])
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=[ValueError("2 validation errors for DomainAgentDecision"), _finalize_decision([finding])]
    )
    critic = AsyncMock(return_value=FindingsVerdict(ok=True, feedback="", calibrated_confidence=0.9))

    with patch.object(base_agent, "_llm", mock_llm), \
         patch.object(base_agent, "critique_findings", critic):
        bundle, tools_used, react_iterations = await base_agent._react_loop(
            _dispatch(), _prep(), [],
            "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}",
        )

    assert bundle.findings == [finding]
    assert bundle.confidence == 0.9
    assert tools_used == []
    assert react_iterations == 2
    assert mock_llm.with_structured_output.return_value.ainvoke.await_count == 2


@pytest.mark.asyncio
async def test_react_loop_skips_critic_when_no_findings():
    from src.main_graph.subgraphs.analysis.agents import base_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize_decision([], confidence=0.4)
    )
    critic = AsyncMock()

    with patch.object(base_agent, "_llm", mock_llm), \
         patch.object(base_agent, "critique_findings", critic):
        bundle, tools_used, react_iterations = await base_agent._react_loop(
            _dispatch(), _prep(), [],
            "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}",
        )

    assert bundle.confidence == 0.4
    assert tools_used == []
    assert react_iterations == 1
    critic.assert_not_awaited()

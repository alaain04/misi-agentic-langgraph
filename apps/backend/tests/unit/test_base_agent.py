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
async def test_agent_run_returns_bundle_on_finalize():
    from src.main_graph.subgraphs.analysis.agents.vulnerability_agent import VulnerabilityAgent

    finding = FindingNote(dep_name="express", severity="high", description="CVE", evidence=[])
    final_decision = DomainAgentDecision(
        tool_calls=[], findings=[finding],
        summary="Found 1 CVE", confidence=0.9, finalize=True, reasoning="done",
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=final_decision)

    with patch("src.main_graph.subgraphs.analysis.agents.base_agent._llm", mock_llm):
        bundle = await VulnerabilityAgent().run(_dispatch(), _prep())

    assert isinstance(bundle, EvidenceBundle)
    assert bundle.domain == "vulnerabilities"
    assert bundle.confidence == 0.9
    assert len(bundle.findings) == 1


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
        bundle = await _react_loop(_dispatch(), _prep(), [npm_audit], "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}")

    assert isinstance(bundle, EvidenceBundle)
    assert bundle.domain == "vulnerabilities"


def test_registry_has_expected_agents():
    from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY
    assert "vulnerability_agent" in REGISTRY
    assert "maintenance_agent" in REGISTRY
    assert "supply_chain_agent" in REGISTRY
    assert "web_research_agent" in REGISTRY


def test_agent_get_tools_returns_list():
    from src.main_graph.subgraphs.analysis.agents.vulnerability_agent import VulnerabilityAgent
    tools = VulnerabilityAgent().get_tools(_prep())
    assert isinstance(tools, list)
    assert len(tools) > 0

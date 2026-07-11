from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.models.results import AgentDispatch, EvidenceBundle, PrepResult, DomainAgentDecision
from src.models.conductor import FindingNote


def _prep() -> PrepResult:
    return PrepResult(
        job_id="j1", repo_path="/tmp/r", project_metadata={},
        manifest_files=[], detected_package_manager="npm",
        dependency_graph={}, sbom_cyclonedx={},
        discovery_summary="s", vector_store_id="vs1",
    )


def _dispatch(agent_type: str = "vulnerability_agent") -> AgentDispatch:
    return AgentDispatch(
        domain="vulnerabilities", hypothesis="check CVEs",
        packages_to_focus=["express"], agent_type=agent_type,
    )


@pytest.mark.asyncio
async def test_run_react_loop_returns_bundle_on_finalize():
    from src.main_graph.subgraphs.analysis.agents.base_agent import run_react_loop

    finding = FindingNote(dep_name="express", severity="high", description="CVE", evidence=[])
    final_decision = DomainAgentDecision(
        tool_calls=[], findings=[finding],
        summary="Found 1 CVE", confidence=0.9, finalize=True, reasoning="done",
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=final_decision)

    with patch("src.main_graph.subgraphs.analysis.agents.base_agent._llm", mock_llm):
        bundle = await run_react_loop(_dispatch(), _prep(), [])

    assert isinstance(bundle, EvidenceBundle)
    assert bundle.domain == "vulnerabilities"
    assert bundle.confidence == 0.9
    assert len(bundle.findings) == 1


def test_agent_registry_has_expected_domains():
    from src.main_graph.subgraphs.analysis.agents.registry import AGENT_REGISTRY
    assert "vulnerability_agent" in AGENT_REGISTRY
    assert "maintenance_agent" in AGENT_REGISTRY
    assert "supply_chain_agent" in AGENT_REGISTRY
    assert "web_research_agent" in AGENT_REGISTRY


def test_get_agent_tools_returns_list():
    from src.main_graph.subgraphs.analysis.agents.registry import get_agent_tools
    tools = get_agent_tools("vulnerability_agent", _prep())
    assert isinstance(tools, list)
    assert len(tools) > 0

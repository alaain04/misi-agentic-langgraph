from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.subgraphs.analysis.agents.maintenance_agent import MaintenanceAgent
from src.models.conductor import FindingNote
from src.models.results import AgentDispatch, EvidenceBundle, PrepResult

_GRAPH = {
    "direct": {"express": "4.18.0"},
    "packages": {
        "express@4.18.0": {"version": "4.18.0", "dependencies": ["qs@6.11.0"]},
        "qs@6.11.0": {"version": "6.11.0", "dependencies": []},
    },
}


def _prep(graph: dict) -> PrepResult:
    return PrepResult(
        job_id="j1",
        repo_path="/tmp/r",
        project_metadata={},
        manifest_files=[],
        detected_package_manager="npm",
        dependency_graph=graph,
        vector_store_id="",
    )


def _dispatch() -> AgentDispatch:
    return AgentDispatch(
        domain="maintenance",
        hypothesis="check stale deps",
        packages_to_focus=["express", "qs"],
        agent_type="maintenance_agent",
    )


def _bundle(findings: list[FindingNote]) -> EvidenceBundle:
    return EvidenceBundle(
        domain="maintenance",
        hypothesis="h",
        packages_to_focus=["express", "qs"],
        findings=findings,
        summary="s",
        confidence=0.9,
    )


@pytest.mark.asyncio
async def test_maintenance_drops_transitive_findings():
    findings = [
        FindingNote(
            dep_name="express", severity="medium", description="stale", evidence=[]
        ),
        FindingNote(dep_name="qs", severity="medium", description="stale", evidence=[]),
    ]
    with patch.object(
        MaintenanceAgent.__bases__[0],
        "run",
        AsyncMock(return_value=(_bundle(findings), ["unmaintained_packages"], 1)),
    ):
        bundle, tools, iters = await MaintenanceAgent().run(_dispatch(), _prep(_GRAPH))

    names = [f.dep_name for f in bundle.findings]
    assert names == ["express"]  # qs (transitive) dropped
    assert tools == ["unmaintained_packages"]


@pytest.mark.asyncio
async def test_maintenance_keeps_all_when_no_transitive_data():
    # package.json fallback: cannot determine directness, keep everything
    graph = {"direct": {"express": "^4"}, "packages": {}}
    findings = [
        FindingNote(
            dep_name="express", severity="medium", description="stale", evidence=[]
        ),
        FindingNote(dep_name="qs", severity="medium", description="stale", evidence=[]),
    ]
    with patch.object(
        MaintenanceAgent.__bases__[0],
        "run",
        AsyncMock(return_value=(_bundle(findings), [], 1)),
    ):
        bundle, _, _ = await MaintenanceAgent().run(_dispatch(), _prep(graph))

    assert {f.dep_name for f in bundle.findings} == {"express", "qs"}

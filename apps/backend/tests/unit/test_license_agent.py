from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.models.results import AgentDispatch, EvidenceBundle, PrepResult


def _prep() -> PrepResult:
    return PrepResult(
        job_id="j1",
        repo_path="/tmp/r",
        project_metadata={},
        manifest_files=[],
        detected_package_manager="npm",
        dependency_graph={
            "direct": {},
            "packages": {
                "gpl-lib@1.0.0": {},
                "mit-lib@2.0.0": {},
                "mystery-lib@3.0.0": {},
                "no-license@4.0.0": {},
            },
        },
        discovery_summary="s",
        vector_store_id="vs1",
    )


def _dispatch() -> AgentDispatch:
    return AgentDispatch(
        domain="licenses",
        hypothesis="check for license conflicts",
        packages_to_focus=["express"],  # must be ignored
        agent_type="license_agent",
    )


@pytest.mark.asyncio
async def test_license_agent_run_end_to_end():
    from src.main_graph.subgraphs.analysis.agents import license_agent

    collected = {
        "gpl-lib@1.0.0": "GPL-3.0-only",
        "mit-lib@2.0.0": "MIT",
        "mystery-lib@3.0.0": "Some Custom License Text",
        "no-license@4.0.0": "UNKNOWN",
    }

    with (
        patch.object(
            license_agent, "collect_licenses", AsyncMock(return_value=collected)
        ),
        patch.object(license_agent, "_load_pkg", return_value={"license": "MIT"}),
    ):
        bundle, tools_used, react_iterations = await license_agent.LicenseAgent().run(
            _dispatch(), _prep()
        )

    assert isinstance(bundle, EvidenceBundle)
    assert bundle.packages_to_focus == []  # packages_to_focus ignored
    assert bundle.confidence == 1.0
    assert tools_used == ["license_collector", "license_rules"]
    assert react_iterations == 1

    by_dep = {}
    for f in bundle.findings:
        by_dep.setdefault(f.dep_name, []).append(f)

    # gpl-lib: C1 (medium), C2 x2 (low), C3 (high) against MIT project
    gpl_severities = sorted(f.severity for f in by_dep["gpl-lib"])
    assert gpl_severities == ["high", "low", "low", "medium"]

    # mit-lib vs MIT project: no conflicts
    assert "mit-lib" not in by_dep

    # unresolvable expression -> info finding, manual review
    assert len(by_dep["mystery-lib"]) == 1
    assert by_dep["mystery-lib"][0].severity == "info"
    assert "curated" in by_dep["mystery-lib"][0].description

    # UNKNOWN license -> info finding
    assert len(by_dep["no-license"]) == 1
    assert by_dep["no-license"][0].severity == "info"

    # most severe first
    assert bundle.findings[0].severity == "high"


@pytest.mark.asyncio
async def test_license_agent_treats_missing_project_license_as_unlicensed():
    from src.main_graph.subgraphs.analysis.agents import license_agent

    collected = {"mit-lib@2.0.0": "MIT"}
    with (
        patch.object(
            license_agent, "collect_licenses", AsyncMock(return_value=collected)
        ),
        patch.object(license_agent, "_load_pkg", return_value={}),  # no "license" field
    ):
        bundle, _, _ = await license_agent.LicenseAgent().run(_dispatch(), _prep())

    # MIT dependency musts include_notice; UNLICENSED project doesn't fulfill it -> C2
    assert any(
        f.severity == "low" and "notice" in f.description for f in bundle.findings
    )


@pytest.mark.asyncio
async def test_license_agent_handles_legacy_dict_shaped_project_license():
    from src.main_graph.subgraphs.analysis.agents import license_agent

    collected = {"gpl-lib@1.0.0": "GPL-3.0-only"}
    with (
        patch.object(
            license_agent, "collect_licenses", AsyncMock(return_value=collected)
        ),
        patch.object(
            license_agent, "_load_pkg", return_value={"license": {"type": "MIT"}}
        ),
    ):
        bundle, _, _ = await license_agent.LicenseAgent().run(_dispatch(), _prep())

    # must not crash, and the dict's "type" must actually resolve to MIT (not fall
    # back to UNLICENSED) -- proven by the same C3 high-severity finding a real
    # MIT project produces against a GPL-3.0-only dependency
    assert any(f.severity == "high" for f in bundle.findings)

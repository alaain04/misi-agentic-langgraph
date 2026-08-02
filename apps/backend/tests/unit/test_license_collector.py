from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from src.main_graph.subgraphs.analysis.agents.license_collector import collect_licenses
from src.models.results import PrepResult


def _prep(**kw) -> PrepResult:
    defaults = dict(
        job_id="j1",
        repo_path="/tmp/r",
        project_metadata={},
        manifest_files=[],
        detected_package_manager="npm",
        dependency_graph={"direct": {}, "packages": {}},
    )
    return PrepResult(**{**defaults, **kw})


def _license_scan_output(*entries: dict) -> dict:
    return {
        "SchemaVersion": 2,
        "Results": [{"Target": "package-lock.json", "Licenses": list(entries)}],
    }


@pytest.mark.asyncio
async def test_collect_licenses_returns_empty_when_no_packages():
    prep = _prep(dependency_graph={"direct": {}, "packages": {}})
    result = await collect_licenses(prep, container=AsyncMock())
    assert result == {}


@pytest.mark.asyncio
async def test_collect_licenses_maps_scan_result_by_package_name():
    prep = _prep(
        dependency_graph={
            "direct": {"lodash": "4.17.15"},
            "packages": {"lodash@4.17.15": {"version": "4.17.15", "dependencies": []}},
        }
    )
    container = AsyncMock()
    container.run.return_value = (
        0,
        json.dumps(_license_scan_output({"PkgName": "lodash", "Name": "MIT"})),
        "",
    )
    result = await collect_licenses(prep, container=container)
    assert result == {"lodash@4.17.15": "MIT"}


@pytest.mark.asyncio
async def test_collect_licenses_marks_unresolved_packages_unknown():
    prep = _prep(
        dependency_graph={
            "direct": {"ghost": "1.0.0"},
            "packages": {"ghost@1.0.0": {"version": "1.0.0", "dependencies": []}},
        }
    )
    container = AsyncMock()
    container.run.return_value = (0, json.dumps(_license_scan_output()), "")
    result = await collect_licenses(prep, container=container)
    assert result == {"ghost@1.0.0": "UNKNOWN"}


@pytest.mark.asyncio
async def test_collect_licenses_applies_same_license_to_every_version():
    prep = _prep(
        dependency_graph={
            "direct": {},
            "packages": {
                "left-pad@1.0.0": {"version": "1.0.0", "dependencies": []},
                "left-pad@1.3.0": {"version": "1.3.0", "dependencies": []},
            },
        }
    )
    container = AsyncMock()
    container.run.return_value = (
        0,
        json.dumps(_license_scan_output({"PkgName": "left-pad", "Name": "MIT"})),
        "",
    )
    result = await collect_licenses(prep, container=container)
    assert result == {"left-pad@1.0.0": "MIT", "left-pad@1.3.0": "MIT"}


@pytest.mark.asyncio
async def test_collect_licenses_returns_unknown_on_scan_error():
    prep = _prep(
        dependency_graph={
            "direct": {"lodash": "4.17.15"},
            "packages": {"lodash@4.17.15": {"version": "4.17.15", "dependencies": []}},
        }
    )
    container = AsyncMock()
    container.run.return_value = (127, "", "sh: trivy: not found")
    result = await collect_licenses(prep, container=container)
    assert result == {"lodash@4.17.15": "UNKNOWN"}

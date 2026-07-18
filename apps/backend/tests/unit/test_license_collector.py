from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.models.results import PrepResult


def _prep(
    package_manager: str, packages: dict, repo_path: str = "/tmp/r"
) -> PrepResult:
    return PrepResult(
        job_id="j1",
        repo_path=repo_path,
        project_metadata={},
        manifest_files=[],
        detected_package_manager=package_manager,
        dependency_graph={"direct": {}, "packages": packages},
        discovery_summary="s",
        vector_store_id="vs1",
    )


def test_collect_licenses_returns_empty_when_no_packages():
    from src.main_graph.subgraphs.analysis.agents.license_collector import (
        collect_licenses,
    )

    result = asyncio.run(collect_licenses(_prep("npm", {})))
    assert result == {}


@pytest.mark.asyncio
async def test_npm_lockfile_license_extraction(tmp_path):
    from src.main_graph.subgraphs.analysis.agents import license_collector

    lock = {
        "packages": {
            "": {},
            "node_modules/express": {"version": "4.18.0", "license": "MIT"},
            "node_modules/lodash": {"version": "4.17.21", "license": "MIT"},
            "node_modules/no-license-field": {"version": "1.0.0"},
        }
    }
    (tmp_path / "package-lock.json").write_text(json.dumps(lock))

    prep = _prep(
        "npm",
        {
            "express@4.18.0": {},
            "lodash@4.17.21": {},
            "no-license-field@1.0.0": {},
        },
        repo_path=str(tmp_path),
    )

    metadata = AsyncMock(return_value={"error": "not found"})
    with patch.object(license_collector, "_npm_metadata", metadata):
        result = await license_collector.collect_licenses(prep)

    assert result["express@4.18.0"] == "MIT"
    assert result["lodash@4.17.21"] == "MIT"
    assert (
        result["no-license-field@1.0.0"] == "UNKNOWN"
    )  # missing field, registry also failed
    metadata.assert_awaited_once_with(
        "no-license-field"
    )  # only the missing one falls back


@pytest.mark.asyncio
async def test_yarn_pnpm_falls_back_to_registry_without_reading_lockfile():
    from src.main_graph.subgraphs.analysis.agents import license_collector

    prep = _prep("yarn", {"left-pad@1.3.0": {}})
    metadata = AsyncMock(return_value={"license": "WTFPL"})

    with patch.object(license_collector, "_npm_metadata", metadata):
        result = await license_collector.collect_licenses(prep)

    assert result == {"left-pad@1.3.0": "WTFPL"}
    metadata.assert_awaited_once_with("left-pad")


@pytest.mark.asyncio
async def test_registry_license_field_as_legacy_object_shape():
    from src.main_graph.subgraphs.analysis.agents import license_collector

    prep = _prep("pnpm", {"old-pkg@1.0.0": {}})
    metadata = AsyncMock(return_value={"license": {"type": "MIT"}})

    with patch.object(license_collector, "_npm_metadata", metadata):
        result = await license_collector.collect_licenses(prep)

    assert result == {"old-pkg@1.0.0": "MIT"}


@pytest.mark.asyncio
async def test_unresolvable_package_recorded_as_unknown():
    from src.main_graph.subgraphs.analysis.agents import license_collector

    prep = _prep("pnpm", {"ghost@0.0.1": {}})
    metadata = AsyncMock(return_value={"error": "404"})

    with patch.object(license_collector, "_npm_metadata", metadata):
        result = await license_collector.collect_licenses(prep)

    assert result == {"ghost@0.0.1": "UNKNOWN"}


@pytest.mark.asyncio
async def test_registry_lookups_bounded_by_concurrency_setting():
    from src.main_graph.subgraphs.analysis.agents import license_collector

    prep = _prep("pnpm", {f"pkg{i}@1.0.0": {} for i in range(5)})
    in_flight = 0
    max_in_flight = 0

    async def fake_metadata(name: str) -> dict:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return {"license": "MIT"}

    with (
        patch.object(license_collector, "_npm_metadata", fake_metadata),
        patch.object(license_collector.settings, "license_lookup_concurrency", 2),
    ):
        await license_collector.collect_licenses(prep)

    assert max_in_flight <= 2

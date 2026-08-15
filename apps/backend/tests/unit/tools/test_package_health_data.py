from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.tools.registry import TOOL_REGISTRY

_NOW = datetime.now(UTC)
_RECENT = (_NOW - timedelta(days=10)).isoformat()
_OLD = (_NOW - timedelta(days=1000)).isoformat()
_VERY_OLD = (_NOW - timedelta(days=4000)).isoformat()


def _meta(created: str, modified: str) -> dict:
    return {"time": {"created": created, "modified": modified}}


@pytest.mark.asyncio
async def test_returns_raw_facts_for_healthy_package():
    meta = _meta(_VERY_OLD, _RECENT)
    with (
        patch(
            "src.main_graph.tools.external_api._load_pkg",
            return_value={"dependencies": {"healthy-pkg": "1.0.0"}},
        ),
        patch(
            "src.main_graph.tools.external_api._npm_metadata",
            AsyncMock(return_value=meta),
        ),
        patch(
            "src.main_graph.tools.external_api._npm_weekly_downloads",
            AsyncMock(return_value=50_000),
        ),
    ):
        result = await TOOL_REGISTRY["package_health_data"](repo_path="/fake")

    assert result["fields"] == [
        "package",
        "days_since_created",
        "days_since_last_release",
        "weekly_downloads",
    ]
    assert result["packages"] == [["healthy-pkg", 4000, 10, 50_000]]
    assert result["errors"] == []
    assert result["checked"] == 1
    assert result["total_deps"] == 1


@pytest.mark.asyncio
async def test_stale_but_high_downloads_returned_without_flagging():
    """The tool does no risk judgment -- a stale, heavily-downloaded package
    (e.g. class-validator) comes back as plain data. Risk judgment is the
    agent's job, done entirely in the prompt now."""
    meta = _meta(_OLD, _OLD)
    with (
        patch(
            "src.main_graph.tools.external_api._load_pkg",
            return_value={"dependencies": {"class-validator": "0.14.0"}},
        ),
        patch(
            "src.main_graph.tools.external_api._npm_metadata",
            AsyncMock(return_value=meta),
        ),
        patch(
            "src.main_graph.tools.external_api._npm_weekly_downloads",
            AsyncMock(return_value=11_998_815),
        ),
    ):
        result = await TOOL_REGISTRY["package_health_data"](repo_path="/fake")

    assert result["packages"] == [["class-validator", 1000, 1000, 11_998_815]]


@pytest.mark.asyncio
async def test_metadata_error_is_returned_not_dropped():
    with (
        patch(
            "src.main_graph.tools.external_api._load_pkg",
            return_value={"dependencies": {"broken-pkg": "1.0.0"}},
        ),
        patch(
            "src.main_graph.tools.external_api._npm_metadata",
            AsyncMock(return_value={"error": "404 Not Found"}),
        ),
        patch(
            "src.main_graph.tools.external_api._npm_weekly_downloads",
            AsyncMock(return_value=None),
        ),
    ):
        result = await TOOL_REGISTRY["package_health_data"](repo_path="/fake")

    assert result["packages"] == []
    assert result["errors"] == [{"package": "broken-pkg", "error": "404 Not Found"}]


@pytest.mark.asyncio
async def test_caps_at_default_limit_and_reports_total():
    dep_names = [f"pkg-{i}" for i in range(20)]
    meta = _meta(_OLD, _RECENT)
    with (
        patch(
            "src.main_graph.tools.external_api._load_pkg",
            return_value={"dependencies": {name: "1.0.0" for name in dep_names}},
        ),
        patch(
            "src.main_graph.tools.external_api._npm_metadata",
            AsyncMock(return_value=meta),
        ),
        patch(
            "src.main_graph.tools.external_api._npm_weekly_downloads",
            AsyncMock(return_value=100),
        ),
    ):
        result = await TOOL_REGISTRY["package_health_data"](repo_path="/fake")

    assert result["checked"] == 12
    assert result["total_deps"] == 20
    assert len(result["packages"]) == 12


@pytest.mark.asyncio
async def test_explicit_packages_argument_bypasses_default_cap():
    dep_names = [f"pkg-{i}" for i in range(20)]
    meta = _meta(_OLD, _RECENT)
    with (
        patch(
            "src.main_graph.tools.external_api._load_pkg",
            return_value={"dependencies": {name: "1.0.0" for name in dep_names}},
        ),
        patch(
            "src.main_graph.tools.external_api._npm_metadata",
            AsyncMock(return_value=meta),
        ),
        patch(
            "src.main_graph.tools.external_api._npm_weekly_downloads",
            AsyncMock(return_value=100),
        ),
    ):
        result = await TOOL_REGISTRY["package_health_data"](
            repo_path="/fake", packages=["pkg-19"]
        )

    assert result["checked"] == 1
    assert result["packages"] == [["pkg-19", 1000, 10, 100]]


def test_package_health_data_is_registered():
    assert "package_health_data" in TOOL_REGISTRY


def test_default_scan_payload_fits_tool_result_budget():
    """Regression guard for the truncation bug this shape was designed to
    avoid: base_agent._format_results truncates each tool result to 1500
    chars (json.dumps(tr.output, indent=2)[:1500]). A full 12-package scan,
    even with a long scoped package name and 8-digit downloads, must stay
    under that budget or the agent silently loses data."""
    import json

    row = ["@some-org/really-long-package-name-example", 4123, 987, 11_998_815]
    result = {
        "fields": [
            "package",
            "days_since_created",
            "days_since_last_release",
            "weekly_downloads",
        ],
        "packages": [row] * 12,
        "errors": [],
        "checked": 12,
        "total_deps": 45,
    }
    assert len(json.dumps(result, indent=2)) < 1500

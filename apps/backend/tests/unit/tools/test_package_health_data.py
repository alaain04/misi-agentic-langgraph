from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.tools.registry import TOOL_REGISTRY

_NOW = datetime.now(UTC)
_RECENT = (_NOW - timedelta(days=10)).isoformat()
_OLD = (_NOW - timedelta(days=1000)).isoformat()


def _meta(
    created: str, modified: str, maintainer_count: int, latest: str = "1.0.0"
) -> dict:
    return {
        "time": {"created": created, "modified": modified},
        "maintainers": [{"name": f"m{i}"} for i in range(maintainer_count)],
        "dist-tags": {"latest": latest},
    }


@pytest.mark.asyncio
async def test_returns_raw_facts_for_healthy_package():
    meta = _meta(_OLD, _RECENT, maintainer_count=2, latest="2.3.1")
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

    assert result["packages"] == [
        {
            "package": "healthy-pkg",
            "created": _OLD,
            "last_modified": _RECENT,
            "weekly_downloads": 50_000,
            "maintainer_count": 2,
            "latest_version": "2.3.1",
        }
    ]
    assert result["checked"] == 1
    assert result["total_deps"] == 1


@pytest.mark.asyncio
async def test_stale_but_high_downloads_returned_without_flagging():
    """The tool does no risk judgment -- a stale, heavily-downloaded package
    (e.g. class-validator) comes back as plain data, not dropped or marked
    risky. Risk judgment moved to the agent's prompt, not the tool."""
    meta = _meta(_OLD, _OLD, maintainer_count=1, latest="0.14.0")
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

    assert result["packages"] == [
        {
            "package": "class-validator",
            "created": _OLD,
            "last_modified": _OLD,
            "weekly_downloads": 11_998_815,
            "maintainer_count": 1,
            "latest_version": "0.14.0",
        }
    ]


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

    assert result["packages"] == [{"package": "broken-pkg", "error": "404 Not Found"}]


def test_package_health_data_is_registered():
    assert "package_health_data" in TOOL_REGISTRY

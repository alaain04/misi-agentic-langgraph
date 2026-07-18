from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.tools.registry import TOOL_REGISTRY

_NOW = datetime.now(UTC)
_RECENT = (_NOW - timedelta(days=10)).isoformat()
_OLD = (_NOW - timedelta(days=1000)).isoformat()


def _meta(created: str, modified: str, maintainer_count: int) -> dict:
    return {
        "time": {"created": created, "modified": modified},
        "maintainers": [{"name": f"m{i}"} for i in range(maintainer_count)],
    }


@pytest.mark.asyncio
async def test_single_maintainer_with_high_downloads_is_not_flagged():
    meta = _meta(_OLD, _RECENT, maintainer_count=1)
    with (
        patch(
            "src.main_graph.tools.external_api._load_pkg",
            return_value={"dependencies": {"popular-pkg": "1.0.0"}},
        ),
        patch(
            "src.main_graph.tools.external_api._npm_metadata",
            AsyncMock(return_value=meta),
        ),
        patch(
            "src.main_graph.tools.external_api._npm_weekly_downloads",
            AsyncMock(return_value=5_000_000),
        ),
    ):
        result = await TOOL_REGISTRY["high_risk_packages"](repo_path="/fake")

    assert result["high_risk"] == []


@pytest.mark.asyncio
async def test_abandoned_package_with_high_downloads_is_not_flagged():
    """A mature, widely-adopted package (e.g. lodash) can go years without a
    release and still not be a risk - healthy downloads override every other
    signal, including abandonment and single-maintainer status."""
    meta = _meta(_OLD, _OLD, maintainer_count=1)
    with (
        patch(
            "src.main_graph.tools.external_api._load_pkg",
            return_value={"dependencies": {"lodash-like": "1.0.0"}},
        ),
        patch(
            "src.main_graph.tools.external_api._npm_metadata",
            AsyncMock(return_value=meta),
        ),
        patch(
            "src.main_graph.tools.external_api._npm_weekly_downloads",
            AsyncMock(return_value=5_000_000),
        ),
    ):
        result = await TOOL_REGISTRY["high_risk_packages"](repo_path="/fake")

    assert result["high_risk"] == []


@pytest.mark.asyncio
async def test_single_maintainer_with_recent_release_is_not_flagged():
    meta = _meta(_OLD, _RECENT, maintainer_count=1)
    with (
        patch(
            "src.main_graph.tools.external_api._load_pkg",
            return_value={"dependencies": {"active-pkg": "1.0.0"}},
        ),
        patch(
            "src.main_graph.tools.external_api._npm_metadata",
            AsyncMock(return_value=meta),
        ),
        patch(
            "src.main_graph.tools.external_api._npm_weekly_downloads",
            AsyncMock(return_value=None),
        ),
    ):
        result = await TOOL_REGISTRY["high_risk_packages"](repo_path="/fake")

    assert result["high_risk"] == []


@pytest.mark.asyncio
async def test_abandoned_and_low_downloads_is_flagged_without_maintainer_reason():
    """Abandonment + low downloads is enough to flag - but maintainer count
    (even a single maintainer) is never itself a reported reason."""
    meta = _meta(_OLD, _OLD, maintainer_count=1)
    with (
        patch(
            "src.main_graph.tools.external_api._load_pkg",
            return_value={"dependencies": {"risky-pkg": "1.0.0"}},
        ),
        patch(
            "src.main_graph.tools.external_api._npm_metadata",
            AsyncMock(return_value=meta),
        ),
        patch(
            "src.main_graph.tools.external_api._npm_weekly_downloads",
            AsyncMock(return_value=5),
        ),
    ):
        result = await TOOL_REGISTRY["high_risk_packages"](repo_path="/fake")

    assert len(result["high_risk"]) == 1
    assert result["high_risk"][0]["package"] == "risky-pkg"
    assert "abandoned (>2 years no release)" in result["high_risk"][0]["reasons"]
    assert not any("maintainer" in r for r in result["high_risk"][0]["reasons"])


def test_high_risk_packages_is_registered():
    assert "high_risk_packages" in TOOL_REGISTRY

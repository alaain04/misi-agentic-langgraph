from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.changelog import (
    _tag_version,
    _tag_in_window,
    fetch_release_notes,
    fetch_release_notes_between,
)


class FakeContainer:
    """Returns queued (rc, stdout, stderr) per run() call, in order."""

    def __init__(self, results):
        self._results = list(results)
        self.commands = []

    async def run(
        self, image, command, volume=None, run_as_root=False, secret_env=None
    ):
        self.commands.append(command)
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_fetch_release_notes_returns_unavailable_when_repo_unresolved():
    container = FakeContainer([(1, "", "npm error 404 Not Found")])
    result = await fetch_release_notes(
        "left-pad", "/repo", container, "node:lts-alpine"
    )
    assert result["available"] is False


@pytest.mark.asyncio
async def test_fetch_release_notes_success():
    container = FakeContainer([(0, "git+https://github.com/eslint/eslint.git\n", "")])
    releases_json = json.dumps(
        [{"tag_name": "v9.0.0", "name": "9.0.0", "body": "breaking: flat config"}]
    ).encode()
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(releases_json, b""))
    fake_proc.returncode = 0

    with patch(
        "src.main_graph.subgraphs.remediation.changelog.asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    ):
        result = await fetch_release_notes(
            "eslint", "/repo", container, "node:lts-alpine"
        )

    assert result["available"] is True
    assert result["repository"] == "eslint/eslint"
    assert result["releases"][0]["tag"] == "v9.0.0"


@pytest.mark.asyncio
async def test_fetch_release_notes_safely_quotes_package_name():
    """Shell metacharacters in a package name must not reach the container
    command unescaped (command injection guard)."""
    container = FakeContainer([(1, "", "npm error 404")])
    malicious_package = "eslint; rm -rf /"
    result = await fetch_release_notes(
        malicious_package, "/repo", container, "node:lts-alpine"
    )

    assert len(container.commands) == 1
    executed_command = container.commands[0]
    assert "'eslint; rm -rf /'" in executed_command or (
        "eslint" in executed_command and "rm -rf" not in executed_command
    )
    assert result["available"] is False


def test_tag_version_strips_v_prefix():
    assert _tag_version("v4.17.21") == (4, 17, 21)
    assert _tag_version("4.17.21") == (4, 17, 21)
    assert _tag_version("release-1.2") is None


def test_tag_in_window_half_open():
    # (1.0.0, 2.0.0]: excludes current, includes target
    assert _tag_in_window("v1.0.0", (1, 0, 0), (2, 0, 0)) is False
    assert _tag_in_window("v1.5.0", (1, 0, 0), (2, 0, 0)) is True
    assert _tag_in_window("v2.0.0", (1, 0, 0), (2, 0, 0)) is True
    assert _tag_in_window("v2.0.1", (1, 0, 0), (2, 0, 0)) is False


@pytest.mark.asyncio
async def test_fetch_between_filters_to_window():
    full = {
        "package_name": "lodash",
        "available": True,
        "repository": "lodash/lodash",
        "releases": [
            {"tag": "v2.0.0", "name": "2", "body": "b"},
            {"tag": "v1.5.0", "name": "1.5", "body": "b"},
            {"tag": "v1.0.0", "name": "1", "body": "b"},
        ],
    }
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.fetch_release_notes",
        AsyncMock(return_value=full),
    ):
        out = await fetch_release_notes_between(
            "lodash", "1.0.0", "2.0.0", "/tmp/repo", None, "img"
        )
    tags = [r["tag"] for r in out["releases"]]
    assert tags == ["v2.0.0", "v1.5.0"]  # v1.0.0 excluded (half-open)


@pytest.mark.asyncio
async def test_fetch_between_unparseable_bounds_returns_unfiltered():
    full = {
        "package_name": "lodash",
        "available": True,
        "releases": [{"tag": "v1.5.0", "name": "x", "body": "b"}],
    }
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.fetch_release_notes",
        AsyncMock(return_value=full),
    ):
        out = await fetch_release_notes_between(
            "lodash", None, None, "/tmp/repo", None, "img"
        )
    assert out["releases"] == full["releases"]

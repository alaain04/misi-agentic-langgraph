from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.changelog import fetch_release_notes


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

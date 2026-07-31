from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.deepagent.tools import (
    make_bump_dependency_tool,
    make_dependents_of_tool,
    make_read_release_notes_tool,
    make_verify_tool,
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
async def test_read_release_notes_returns_unavailable_when_repo_unresolved():
    container = FakeContainer([(1, "", "npm error 404 Not Found")])
    tool = make_read_release_notes_tool("/repo", container, "node:lts-alpine")
    result = await tool.ainvoke({"package_name": "left-pad"})
    assert result["available"] is False


@pytest.mark.asyncio
async def test_read_release_notes_success():
    container = FakeContainer([(0, "git+https://github.com/eslint/eslint.git\n", "")])
    releases_json = json.dumps(
        [{"tag_name": "v9.0.0", "name": "9.0.0", "body": "breaking: flat config"}]
    ).encode()
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(releases_json, b""))
    fake_proc.returncode = 0

    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.tools.asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    ):
        tool = make_read_release_notes_tool("/repo", container, "node:lts-alpine")
        result = await tool.ainvoke({"package_name": "eslint"})

    assert result["available"] is True
    assert result["repository"] == "eslint/eslint"
    assert result["releases"][0]["tag"] == "v9.0.0"


@pytest.mark.asyncio
async def test_dependents_of_tool_delegates_to_pure_function():
    graph = {
        "direct": {"a": "1.0.0"},
        "packages": {
            "a@1.0.0": {"dependencies": ["b@1.0.0"]},
            "b@1.0.0": {"dependencies": []},
        },
    }
    tool = make_dependents_of_tool(graph)
    result = await tool.ainvoke({"package_name": "b"})
    assert result == ["a"]


@pytest.mark.asyncio
async def test_bump_dependency_tool_reports_not_applied(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {}}')
    tool = make_bump_dependency_tool(str(tmp_path))
    result = await tool.ainvoke({"target_dep": "left-pad", "to_range": "^2.0.0"})
    assert result == {"applied": False}


@pytest.mark.asyncio
async def test_bump_dependency_tool_applies(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"left-pad": "^1.0.0"}})
    )
    tool = make_bump_dependency_tool(str(tmp_path))
    result = await tool.ainvoke({"target_dep": "left-pad", "to_range": "^2.0.0"})
    assert result == {"applied": True}
    updated = json.loads((tmp_path / "package.json").read_text())
    assert updated["dependencies"]["left-pad"] == "^2.0.0"


@pytest.mark.asyncio
async def test_verify_tool_reports_installed(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {}}))
    container = FakeContainer([(0, "", ""), (0, "{}", "")])
    tool = make_verify_tool(
        str(tmp_path), container, "node:lts-alpine", "npm", ["eslint"]
    )
    result = await tool.ainvoke({})
    assert result["installed"] is True


@pytest.mark.asyncio
async def test_read_release_notes_safely_quotes_package_name():
    """Verify that shell metacharacters in package names are safely quoted,
    preventing command injection."""
    container = FakeContainer([(1, "", "npm error 404")])
    tool = make_read_release_notes_tool("/repo", container, "node:lts-alpine")
    # Use a package name with shell metacharacters
    malicious_package = "eslint; rm -rf /"
    result = await tool.ainvoke({"package_name": malicious_package})

    # Verify the command was escaped, not executed as-is
    assert len(container.commands) == 1
    executed_command = container.commands[0]
    # The command should contain the quoted package name, not raw metacharacters
    assert "'eslint; rm -rf /'" in executed_command or (
        "eslint" in executed_command and "rm -rf" not in executed_command
    )
    # Verify it still failed gracefully without injection
    assert result["available"] is False

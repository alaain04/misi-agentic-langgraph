from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from src.main_graph.subgraphs.remediation.deepagent.tools import (
    make_bump_dependency_tool,
    make_commit_outcome_tool,
    make_dependents_of_tool,
    make_read_release_notes_tool,
    make_verify_tool,
)
from src.models.remediation import RemediationOutcome


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
async def test_read_release_notes_tool_delegates_to_fetch_release_notes():
    container = MagicMock()
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.tools.fetch_release_notes",
        AsyncMock(
            return_value={
                "package_name": "eslint",
                "available": True,
                "repository": "eslint/eslint",
                "releases": [],
            }
        ),
    ) as mock_fetch:
        tool = make_read_release_notes_tool("/repo", container, "node:lts-alpine")
        result = await tool.ainvoke({"package_name": "eslint"})

    mock_fetch.assert_awaited_once_with(
        "eslint", "/repo", container, "node:lts-alpine", None
    )
    assert result["available"] is True


@pytest.mark.asyncio
async def test_read_release_notes_tool_reuses_repo_resolved_by_classify():
    """classify already resolved eslint's GitHub repo via resolve_package_info
    -- the tool must pass that through to fetch_release_notes instead of
    letting it re-resolve the same package with another npm-view container
    spawn."""
    container = MagicMock()
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.tools.fetch_release_notes",
        AsyncMock(
            return_value={
                "package_name": "eslint",
                "available": True,
                "repository": "eslint/eslint",
                "releases": [],
            }
        ),
    ) as mock_fetch:
        tool = make_read_release_notes_tool(
            "/repo",
            container,
            "node:lts-alpine",
            {"eslint": ("eslint", "eslint")},
        )
        await tool.ainvoke({"package_name": "eslint"})

    mock_fetch.assert_awaited_once_with(
        "eslint", "/repo", container, "node:lts-alpine", ("eslint", "eslint")
    )


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
async def test_commit_outcome_writes_outcome_to_state():
    tool = make_commit_outcome_tool()
    outcome = RemediationOutcome(
        strategy="bump_with_codemod",
        to_range="^5.0.0",
        code_diff="--- a\n+++ b\n",
        status="skipped",
        summary="adapted call sites",
    )
    result = await tool.ainvoke(
        {
            "name": "commit_outcome",
            "args": {"target_dep": "lodash", "outcome": outcome},
            "id": "call_1",
            "type": "tool_call",
        }
    )
    assert isinstance(result, Command)
    assert result.update["outcomes"]["lodash"]["to_range"] == "^5.0.0"
    assert result.update["outcomes"]["lodash"]["code_diff"] == "--- a\n+++ b\n"
    messages = result.update["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], ToolMessage)
    assert messages[0].tool_call_id == "call_1"

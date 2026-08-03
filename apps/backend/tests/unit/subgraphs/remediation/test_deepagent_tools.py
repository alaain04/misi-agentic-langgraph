from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.types import Command

from src.main_graph.subgraphs.remediation.deepagent.tools import (
    make_bump_dependency_tool,
    make_commit_plan_tool,
    make_dependents_of_tool,
    make_read_release_notes_tool,
    make_verify_tool,
)
from src.models.remediation import MigrationPlan, MigrationTask


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

    mock_fetch.assert_awaited_once_with("eslint", "/repo", container, "node:lts-alpine")
    assert result["available"] is True


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
async def test_commit_plan_writes_plan_to_state():
    tool = make_commit_plan_tool()
    plan = MigrationPlan(
        target_dep="lodash",
        tier_hint="r2",
        tasks=[MigrationTask(kind="bump", rationale="x", to_range="^4.17.21")],
    )
    result = await tool.ainvoke({"plan": plan})
    assert isinstance(result, Command)
    assert result.update["migration_plans"]["lodash"]["tier_hint"] == "r2"

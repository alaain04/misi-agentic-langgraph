import json
import os
from unittest.mock import AsyncMock, patch

import pytest

import src.main_graph.tools.npm_cli  # trigger registration
from src.main_graph.tools.registry import TOOL_REGISTRY


@pytest.mark.asyncio
async def test_npm_list_parses_json_output():
    fake_output = '{"version": "1.0.0", "dependencies": {"lodash": {"version": "4.17.21"}}}'
    with patch("src.main_graph.tools.npm_cli._run_npm", new=AsyncMock(return_value=(fake_output, ""))):
        result = await TOOL_REGISTRY["npm_list"](repo_path="/tmp/repo")
    assert result["dependencies"]["lodash"]["version"] == "4.17.21"


@pytest.mark.asyncio
async def test_npm_list_returns_error_on_failure():
    with patch("src.main_graph.tools.npm_cli._run_npm", new=AsyncMock(side_effect=Exception("cmd failed"))):
        result = await TOOL_REGISTRY["npm_list"](repo_path="/tmp/repo")
    assert "error" in result


@pytest.mark.asyncio
async def test_npm_audit_parses_vulnerabilities():
    fake_output = '{"vulnerabilities": {"lodash": {"severity": "high", "name": "lodash"}}, "metadata": {"vulnerabilities": {"high": 1}}}'
    with patch("src.main_graph.tools.npm_cli._run_npm", new=AsyncMock(return_value=(fake_output, ""))):
        result = await TOOL_REGISTRY["npm_audit"](repo_path="/tmp/repo")
    assert result["metadata"]["vulnerabilities"]["high"] == 1


@pytest.mark.asyncio
async def test_npm_outdated_parses_output():
    fake_output = '{"lodash": {"current": "4.17.20", "latest": "4.17.21", "wanted": "4.17.21"}}'
    with patch("src.main_graph.tools.npm_cli._run_npm", new=AsyncMock(return_value=(fake_output, ""))):
        result = await TOOL_REGISTRY["npm_outdated"](repo_path="/tmp/repo")
    assert "lodash" in result["outdated"]


def test_tools_are_registered():
    for name in ("npm_list", "npm_audit", "npm_outdated"):
        assert name in TOOL_REGISTRY


@pytest.fixture
def repo_with_pkg(tmp_path):
    pkg = {
        "name": "my-app",
        "dependencies": {"express": "^4.18.0"},
        "devDependencies": {"jest": "^29.0.0"},
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    return str(tmp_path)


@pytest.mark.asyncio
async def test_resolve_transitive_parent_direct_dep(repo_with_pkg):
    """express is a direct dep — is_direct should be True."""
    with patch("src.main_graph.tools.npm_cli._run_npm", new=AsyncMock(return_value=("{}", ""))):
        result = await TOOL_REGISTRY["resolve_transitive_parent"](
            repo_path=repo_with_pkg, package_name="express"
        )
    assert result["is_direct"] is True
    assert result["brought_in_by"] == []


@pytest.mark.asyncio
async def test_resolve_transitive_parent_transitive_dep(repo_with_pkg):
    """accepts is a transitive dep brought in by express."""
    npm_tree = json.dumps({
        "name": "my-app",
        "dependencies": {
            "express": {
                "version": "4.18.2",
                "dependencies": {
                    "accepts": {"version": "1.3.8", "dependencies": {}}
                }
            }
        }
    })
    with patch("src.main_graph.tools.npm_cli._run_npm", new=AsyncMock(return_value=(npm_tree, ""))):
        result = await TOOL_REGISTRY["resolve_transitive_parent"](
            repo_path=repo_with_pkg, package_name="accepts"
        )
    assert result["is_direct"] is False
    assert "express" in result["brought_in_by"]


@pytest.mark.asyncio
async def test_resolve_transitive_parent_unknown_package(repo_with_pkg):
    """Package not found anywhere returns empty parents."""
    npm_tree = json.dumps({"name": "my-app", "dependencies": {}})
    with patch("src.main_graph.tools.npm_cli._run_npm", new=AsyncMock(return_value=(npm_tree, ""))):
        result = await TOOL_REGISTRY["resolve_transitive_parent"](
            repo_path=repo_with_pkg, package_name="ghost-package"
        )
    assert result["is_direct"] is False
    assert result["brought_in_by"] == []

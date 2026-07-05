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

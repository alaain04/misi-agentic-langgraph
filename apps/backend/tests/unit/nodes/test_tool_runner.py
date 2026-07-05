from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.nodes.tool_runner import tool_runner
from src.models.conductor import ConductorDecision, ToolCall, ToolResult


def _make_state(tool_calls: list[ToolCall], repo_path: str = "/tmp/repo"):
    decision = ConductorDecision(
        tool_calls=tool_calls, findings=[], ask_user=None,
        checkpoint_message=None, finalize=False, reasoning="r",
    )
    return {
        "repo_url": "https://github.com/test/repo",
        "concern": "security",
        "job_id": "j1",
        "autopilot": False,
        "repo_path": repo_path,
        "tool_results": [],
        "findings": [],
        "conductor_iteration": 1,
        "messages": [],
        "conductor_decision": decision,
    }


@pytest.mark.asyncio
async def test_tool_runner_executes_registered_tool():
    fake_output = {"deps": {"lodash": "4.17.21"}}
    tc = ToolCall(tool="npm_list", args={}, reason="check deps")
    with patch("src.main_graph.nodes.tool_runner.TOOL_REGISTRY", {"npm_list": AsyncMock(return_value=fake_output)}):
        result = await tool_runner(_make_state([tc]), config={})
    assert len(result["tool_results"]) == 1
    tr: ToolResult = result["tool_results"][0]
    assert tr.tool == "npm_list"
    assert tr.output == fake_output
    assert tr.error is None


@pytest.mark.asyncio
async def test_tool_runner_captures_error_for_unknown_tool():
    tc = ToolCall(tool="nonexistent_tool", args={}, reason="test")
    result = await tool_runner(_make_state([tc]), config={})
    assert len(result["tool_results"]) == 1
    tr: ToolResult = result["tool_results"][0]
    assert tr.error is not None
    assert "not found" in tr.error


@pytest.mark.asyncio
async def test_tool_runner_runs_multiple_tools_in_parallel():
    import asyncio
    call_times = []

    async def slow_tool(**_kwargs):
        call_times.append(asyncio.get_event_loop().time())
        await asyncio.sleep(0.05)
        return {"ok": True}

    tcs = [
        ToolCall(tool="tool_a", args={}, reason="a"),
        ToolCall(tool="tool_b", args={}, reason="b"),
    ]
    fake_registry = {"tool_a": slow_tool, "tool_b": slow_tool}
    with patch("src.main_graph.nodes.tool_runner.TOOL_REGISTRY", fake_registry):
        import time
        start = time.monotonic()
        result = await tool_runner(_make_state(tcs), config={})
        elapsed = time.monotonic() - start
    assert len(result["tool_results"]) == 2
    # Parallel execution should be ~50ms, not ~100ms
    assert elapsed < 0.08, f"tools ran sequentially: {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_tool_runner_sets_duration_ms():
    tc = ToolCall(tool="npm_list", args={}, reason="check")
    with patch("src.main_graph.nodes.tool_runner.TOOL_REGISTRY", {"npm_list": AsyncMock(return_value={})}):
        result = await tool_runner(_make_state([tc]), config={})
    assert result["tool_results"][0].duration_ms >= 0

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.main_graph.subgraphs.remediation.release_research import (
    fetch_doc,
    make_fetch_doc_tool,
)


def _resp(status_code: int, text: str = "", location: str | None = None):
    headers = {"location": location} if location else {}
    return httpx.Response(status_code, text=text, headers=headers)


@pytest.mark.asyncio
async def test_fetch_doc_rejects_non_http_scheme():
    result = await fetch_doc("file:///etc/passwd")
    assert result["available"] is False
    assert "scheme" in result["error"]


@pytest.mark.asyncio
async def test_fetch_doc_rejects_private_ip_host():
    with patch(
        "socket.getaddrinfo",
        return_value=[(None, None, None, None, ("10.0.0.5", 0))],
    ):
        result = await fetch_doc("http://internal.example.com/MIGRATION.md")
    assert result["available"] is False
    assert "public" in result["error"]


@pytest.mark.asyncio
async def test_fetch_doc_rejects_metadata_ip_host():
    with patch(
        "socket.getaddrinfo",
        return_value=[(None, None, None, None, ("169.254.169.254", 0))],
    ):
        result = await fetch_doc("http://metadata.internal/latest/meta-data/")
    assert result["available"] is False


@pytest.mark.asyncio
async def test_fetch_doc_rejects_unresolvable_host():
    with patch("socket.getaddrinfo", side_effect=OSError("name resolution failed")):
        result = await fetch_doc("http://does-not-exist.invalid/doc.md")
    assert result["available"] is False


@pytest.mark.asyncio
async def test_fetch_doc_success_returns_capped_body():
    with (
        patch(
            "socket.getaddrinfo",
            return_value=[(None, None, None, None, ("140.82.121.3", 0))],
        ),
        patch(
            "httpx.AsyncClient.get",
            AsyncMock(return_value=_resp(200, text="x" * 5000)),
        ),
    ):
        result = await fetch_doc(
            "https://raw.githubusercontent.com/eslint/eslint/main/MIGRATION.md"
        )
    assert result["available"] is True
    assert len(result["body"]) == 2000


@pytest.mark.asyncio
async def test_fetch_doc_attaches_gh_token_only_for_github_hosts():
    captured = {}

    async def _fake_get(self, url, headers=None):
        captured["headers"] = headers
        return _resp(200, text="ok")

    with (
        patch(
            "socket.getaddrinfo",
            return_value=[(None, None, None, None, ("140.82.121.3", 0))],
        ),
        patch("httpx.AsyncClient.get", _fake_get),
        patch(
            "src.main_graph.subgraphs.remediation.release_research.settings"
        ) as mock_settings,
    ):
        mock_settings.github_token = "ghp_test"
        await fetch_doc("https://github.com/eslint/eslint/blob/main/MIGRATION.md")
    assert captured["headers"].get("Authorization") == "Bearer ghp_test"

    captured.clear()
    with (
        patch(
            "socket.getaddrinfo",
            return_value=[(None, None, None, None, ("93.184.216.34", 0))],
        ),
        patch("httpx.AsyncClient.get", _fake_get),
        patch(
            "src.main_graph.subgraphs.remediation.release_research.settings"
        ) as mock_settings,
    ):
        mock_settings.github_token = "ghp_test"
        await fetch_doc("https://example.com/docs/upgrade.md")
    assert "Authorization" not in captured["headers"]


@pytest.mark.asyncio
async def test_fetch_doc_validates_redirect_target_before_following():
    """A redirect to a private IP must be rejected, not silently followed --
    the whole point of disabling auto-follow-redirects."""
    calls = {"n": 0}

    async def _fake_get(self, url, headers=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp(302, location="http://169.254.169.254/latest/meta-data/")
        raise AssertionError("must not follow the redirect to a private IP")

    with (
        patch(
            "socket.getaddrinfo",
            side_effect=[
                # initial host: public
                [(None, None, None, None, ("93.184.216.34", 0))],
                # redirect target
                [(None, None, None, None, ("169.254.169.254", 0))],
            ],
        ),
        patch("httpx.AsyncClient.get", _fake_get),
    ):
        result = await fetch_doc("https://example.com/redirect-to-metadata")
    assert result["available"] is False


@pytest.mark.asyncio
async def test_fetch_doc_gives_up_after_max_redirects():
    async def _fake_get(self, url, headers=None):
        return _resp(302, location="https://example.com/next")

    with (
        patch(
            "socket.getaddrinfo",
            return_value=[(None, None, None, None, ("93.184.216.34", 0))],
        ),
        patch("httpx.AsyncClient.get", _fake_get),
    ):
        result = await fetch_doc("https://example.com/loop")
    assert result["available"] is False
    assert "redirect" in result["error"]


@pytest.mark.asyncio
async def test_make_fetch_doc_tool_delegates_to_fetch_doc():
    tool = make_fetch_doc_tool()
    with patch(
        "src.main_graph.subgraphs.remediation.release_research.fetch_doc",
        AsyncMock(return_value={"available": True, "url": "u", "body": "b"}),
    ) as mock_fetch:
        result = await tool.ainvoke({"url": "https://example.com/doc.md"})
    mock_fetch.assert_awaited_once_with("https://example.com/doc.md")
    assert result["available"] is True

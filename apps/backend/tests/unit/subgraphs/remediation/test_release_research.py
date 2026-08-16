from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.nodes.release_research import (
    ReleaseResearchDecision,
    fetch_doc,
    make_fetch_doc_tool,
    make_get_release_notes_tool,
    research_releases_node,
)
from src.models.conductor import ToolCall
from src.models.remediation import ReleaseDigest


def _decision(**overrides) -> ReleaseResearchDecision:
    defaults = dict(
        tool_calls=[],
        finalize=True,
        migration_needed=False,
        migration_guide="",
        breaking_changes=[],
        reasoning="done",
    )
    defaults.update(overrides)
    return ReleaseResearchDecision(**defaults)


def _prep(**overrides):
    from src.models.results import PrepResult

    defaults = dict(
        id="prep-1",
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={},
        manifest_files=["package.json"],
        package_manager="npm",
        docker_image="node:lts-alpine",
        dependency_graph={"direct": {}, "packages": {}},
    )
    defaults.update(overrides)
    return PrepResult(**defaults)


class _FakeStreamResponse:
    def __init__(
        self, status_code: int, body: bytes = b"", location: str | None = None
    ):
        self.status_code = status_code
        self.headers = {"location": location} if location else {}
        self._body = body

    async def aiter_bytes(self):
        # Yield in chunks to exercise the early-stop-at-cap logic realistically.
        chunk_size = 512
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _fake_stream(status_code: int, body: str = "", location: str | None = None):
    def _stream(self, method, url, headers=None, extensions=None):
        return _FakeStreamResponse(status_code, body.encode(), location)

    return _stream


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
        patch("httpx.AsyncClient.stream", _fake_stream(200, "x" * 5000)),
    ):
        result = await fetch_doc(
            "https://raw.githubusercontent.com/eslint/eslint/main/MIGRATION.md"
        )
    assert result["available"] is True
    assert len(result["body"]) == 2000


@pytest.mark.asyncio
async def test_fetch_doc_attaches_gh_token_only_for_github_hosts():
    captured = {}

    def _stream(self, method, url, headers=None, extensions=None):
        captured["headers"] = headers
        return _FakeStreamResponse(200, b"ok")

    with (
        patch(
            "socket.getaddrinfo",
            return_value=[(None, None, None, None, ("140.82.121.3", 0))],
        ),
        patch("httpx.AsyncClient.stream", _stream),
        patch(
            "src.main_graph.subgraphs.remediation.nodes.release_research.settings"
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
        patch("httpx.AsyncClient.stream", _stream),
        patch(
            "src.main_graph.subgraphs.remediation.nodes.release_research.settings"
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

    def _stream(self, method, url, headers=None, extensions=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeStreamResponse(
                302, location="http://169.254.169.254/latest/meta-data/"
            )
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
        patch("httpx.AsyncClient.stream", _stream),
    ):
        result = await fetch_doc("https://example.com/redirect-to-metadata")
    assert result["available"] is False


@pytest.mark.asyncio
async def test_fetch_doc_gives_up_after_max_redirects():
    with (
        patch(
            "socket.getaddrinfo",
            return_value=[(None, None, None, None, ("93.184.216.34", 0))],
        ),
        patch(
            "httpx.AsyncClient.stream",
            _fake_stream(302, location="https://example.com/next"),
        ),
    ):
        result = await fetch_doc("https://example.com/loop")
    assert result["available"] is False
    assert "redirect" in result["error"]


@pytest.mark.asyncio
async def test_fetch_doc_connects_to_resolved_ip_not_hostname():
    """The whole point of the DNS-rebinding fix: the actual connection
    target must be the validated IP, not a second hostname lookup."""
    captured = {}

    def _stream(self, method, url, headers=None, extensions=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["extensions"] = extensions
        return _FakeStreamResponse(200, b"ok")

    with (
        patch(
            "socket.getaddrinfo",
            return_value=[(None, None, None, None, ("93.184.216.34", 0))],
        ),
        patch("httpx.AsyncClient.stream", _stream),
    ):
        result = await fetch_doc("https://example.com/doc.md")

    assert result["available"] is True
    assert "93.184.216.34" in captured["url"]
    assert "example.com" not in captured["url"]
    assert captured["headers"]["Host"] == "example.com"
    assert captured["extensions"]["sni_hostname"] == "example.com"


@pytest.mark.asyncio
async def test_fetch_doc_stops_reading_once_cap_reached():
    """A host that tries to send far more than the cap must not have the
    full body buffered before truncation -- the read itself should stop
    once the cap is reached, not just slice after."""
    huge_body = b"x" * 1_000_000  # far larger than the 2000-char cap

    with (
        patch(
            "socket.getaddrinfo",
            return_value=[(None, None, None, None, ("93.184.216.34", 0))],
        ),
        patch("httpx.AsyncClient.stream", _fake_stream(200, huge_body.decode())),
    ):
        result = await fetch_doc("https://example.com/huge.md")

    assert result["available"] is True
    assert len(result["body"]) == 2000


@pytest.mark.asyncio
async def test_make_fetch_doc_tool_delegates_to_fetch_doc():
    tool = make_fetch_doc_tool()
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.release_research.fetch_doc",
        AsyncMock(return_value={"available": True, "url": "u", "body": "b"}),
    ) as mock_fetch:
        result = await tool.ainvoke({"url": "https://example.com/doc.md"})
    mock_fetch.assert_awaited_once_with("https://example.com/doc.md")
    assert result["available"] is True


@pytest.mark.asyncio
async def test_get_release_notes_tool_delegates_with_closed_over_args():
    container = MagicMock()
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.release_research.fetch_release_notes_page",
        AsyncMock(
            return_value={
                "available": True,
                "page": 1,
                "has_more": False,
                "releases": [],
            }
        ),
    ) as mock_fetch:
        tool = make_get_release_notes_tool(
            "eslint",
            "7.0.0",
            "8.0.0",
            ("eslint", "eslint"),
            "/repo",
            container,
            "node:lts-alpine",
        )
        result = await tool.ainvoke({"page": 1})

    mock_fetch.assert_awaited_once_with(
        "eslint",
        1,
        "7.0.0",
        "8.0.0",
        "/repo",
        container,
        "node:lts-alpine",
        resolved_repo=("eslint", "eslint"),
    )
    assert result["available"] is True


@pytest.mark.asyncio
async def test_get_release_notes_tool_refuses_page_beyond_ten():
    tool = make_get_release_notes_tool(
        "eslint", "7.0.0", "8.0.0", None, "/repo", MagicMock(), "node:lts-alpine"
    )
    result = await tool.ainvoke({"page": 11})
    assert result["available"] is False
    assert "page limit" in result["error"]


@pytest.mark.asyncio
async def test_research_releases_node_finalizes_immediately_when_told():
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    target = {
        "target_dep": "eslint",
        "addresses": ["eslint"],
        "current_range": "7.0.0",
        "latest_version": "8.0.0",
        "resolved_repo": ("eslint", "eslint"),
        "tier": None,
    }
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_decision(
            migration_needed=True,
            migration_guide="switch to flat config",
            breaking_changes=["flat config replaces .eslintrc"],
        )
    )

    with patch(
        "src.main_graph.subgraphs.remediation.nodes.release_research._llm", mock_llm
    ):
        result = await research_releases_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
                "targets": {"eslint": target},
                "investigations": {},
            },
            config,
        )

    inv = result["investigations"]["eslint"]
    assert inv["release"]["migration_needed"] is True
    assert inv["release"]["migration_guide"] == "switch to flat config"
    mock_llm.with_structured_output.assert_called_once()


@pytest.mark.asyncio
async def test_research_releases_node_skips_r3_targets():
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    target = {
        "target_dep": "matcha",
        "addresses": ["matcha"],
        "current_range": "0.7.0",
        "latest_version": "0.7.0",
        "resolved_repo": None,
        "tier": "r3",
    }
    mock_llm = MagicMock()

    with patch(
        "src.main_graph.subgraphs.remediation.nodes.release_research._llm", mock_llm
    ):
        result = await research_releases_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
                "targets": {"matcha": target},
                "investigations": {},
            },
            config,
        )

    assert result["investigations"] == {}
    mock_llm.with_structured_output.assert_not_called()


@pytest.mark.asyncio
async def test_research_releases_node_iterates_tool_calls_before_finalizing():
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    target = {
        "target_dep": "eslint",
        "addresses": ["eslint"],
        "current_range": "7.0.0",
        "latest_version": "8.0.0",
        "resolved_repo": ("eslint", "eslint"),
        "tier": None,
    }
    mock_llm = MagicMock()
    responses = [
        _decision(
            finalize=False,
            tool_calls=[
                ToolCall(
                    tool="get_release_notes", args={"page": 1}, reason="check notes"
                )
            ],
        ),
        _decision(migration_needed=False),
    ]
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=responses
    )

    with (
        patch(
            "src.main_graph.subgraphs.remediation.nodes.release_research._llm",
            mock_llm,
        ),
        patch(
            "src.main_graph.subgraphs.remediation.nodes.release_research.fetch_release_notes_page",
            AsyncMock(
                return_value={
                    "available": True,
                    "page": 1,
                    "has_more": False,
                    "releases": [],
                }
            ),
        ),
    ):
        result = await research_releases_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
                "targets": {"eslint": target},
                "investigations": {},
            },
            config,
        )

    assert mock_llm.with_structured_output.return_value.ainvoke.await_count == 2
    assert result["investigations"]["eslint"]["release"]["migration_needed"] is False


@pytest.mark.asyncio
async def test_research_releases_node_sources_guide_from_linked_doc():
    """The concrete scenario this whole node exists for: a release body
    just points at MIGRATION.md instead of describing the change -- the
    agent must be able to fetch_doc it and ground migration_guide in what
    that doc actually says, not the release body's pointer text."""
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    target = {
        "target_dep": "eslint",
        "addresses": ["eslint"],
        "current_range": "7.0.0",
        "latest_version": "8.0.0",
        "resolved_repo": ("eslint", "eslint"),
        "tier": None,
    }
    mock_llm = MagicMock()
    responses = [
        # 1: reads release notes, sees a pointer to MIGRATION.md
        _decision(
            finalize=False,
            tool_calls=[
                ToolCall(
                    tool="get_release_notes", args={"page": 1}, reason="check notes"
                )
            ],
        ),
        # 2: notes just said "see MIGRATION.md" -- fetches it
        _decision(
            finalize=False,
            tool_calls=[
                ToolCall(
                    tool="fetch_doc",
                    args={
                        "url": "https://raw.githubusercontent.com/eslint/eslint/main/MIGRATION.md"
                    },
                    reason="release body points here",
                )
            ],
        ),
        # 3: grounds the guide in the doc's actual content
        _decision(
            migration_needed=True,
            migration_guide=(
                "Replace .eslintrc with eslint.config.js (from MIGRATION.md)"
            ),
            breaking_changes=["flat config replaces .eslintrc"],
        ),
    ]
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=responses
    )

    with (
        patch(
            "src.main_graph.subgraphs.remediation.nodes.release_research._llm",
            mock_llm,
        ),
        patch(
            "src.main_graph.subgraphs.remediation.nodes.release_research.fetch_release_notes_page",
            AsyncMock(
                return_value={
                    "available": True,
                    "page": 1,
                    "has_more": False,
                    "releases": [
                        {"tag": "v8.0.0", "name": "8.0.0", "body": "see MIGRATION.md"}
                    ],
                }
            ),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.nodes.release_research.fetch_doc",
            AsyncMock(
                return_value={
                    "available": True,
                    "url": "https://raw.githubusercontent.com/eslint/eslint/main/MIGRATION.md",
                    "body": "Replace .eslintrc with eslint.config.js.",
                }
            ),
        ) as mock_fetch_doc,
    ):
        result = await research_releases_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
                "targets": {"eslint": target},
                "investigations": {},
            },
            config,
        )

    mock_fetch_doc.assert_awaited_once_with(
        "https://raw.githubusercontent.com/eslint/eslint/main/MIGRATION.md"
    )
    inv = result["investigations"]["eslint"]
    assert inv["release"]["migration_needed"] is True
    assert "MIGRATION.md" in inv["release"]["migration_guide"]
    assert mock_llm.with_structured_output.return_value.ainvoke.await_count == 3


@pytest.mark.asyncio
async def test_research_releases_node_falls_back_conservatively_on_failure():
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    target = {
        "target_dep": "eslint",
        "addresses": ["eslint"],
        "current_range": "7.0.0",
        "latest_version": "8.0.0",
        "resolved_repo": ("eslint", "eslint"),
        "tier": None,
    }
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=RuntimeError("LLM provider timeout")
    )

    with patch(
        "src.main_graph.subgraphs.remediation.nodes.release_research._llm", mock_llm
    ):
        result = await research_releases_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
                "targets": {"eslint": target},
                "investigations": {},
            },
            config,
        )

    inv = result["investigations"]["eslint"]
    assert inv["release"]["migration_needed"] is True
    assert "research failed" in inv["release"]["breaking_changes"][0]


@pytest.mark.asyncio
async def test_research_releases_node_preserves_existing_call_sites_and_dependents():
    """select_targets_node already populated dependents/call_sites --
    research_releases_node must only overwrite `release`, not clobber them."""
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    target = {
        "target_dep": "eslint",
        "addresses": ["eslint"],
        "current_range": "7.0.0",
        "latest_version": "8.0.0",
        "resolved_repo": None,
        "tier": None,
    }
    existing_investigation = {
        "target_dep": "eslint",
        "dependents": ["some-consumer"],
        "call_sites": ["src/x.ts:1"],
        "release": ReleaseDigest(
            from_version="7.0.0", to_version="8.0.0", migration_needed=False
        ).model_dump(),
    }
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_decision(migration_needed=True, breaking_changes=["x"])
    )

    with patch(
        "src.main_graph.subgraphs.remediation.nodes.release_research._llm", mock_llm
    ):
        result = await research_releases_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
                "targets": {"eslint": target},
                "investigations": {"eslint": existing_investigation},
            },
            config,
        )

    inv = result["investigations"]["eslint"]
    assert inv["dependents"] == ["some-consumer"]
    assert inv["call_sites"] == ["src/x.ts:1"]
    assert inv["release"]["migration_needed"] is True

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── _search_npm ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_npm_returns_package_list():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "objects": [
            {
                "package": {
                    "name": "fastify",
                    "description": "Fast HTTP framework",
                    "date": "2024-01-01T00:00:00Z",
                }
            }
        ]
    }
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch(
        "src.main_graph.nodes.recommendation_tools.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from src.main_graph.nodes.recommendation_tools import _search_npm

        result = await _search_npm("http framework", max_results=5)

    assert len(result) == 1
    assert result[0]["name"] == "fastify"
    assert result[0]["description"] == "Fast HTTP framework"
    assert result[0]["last_publish"] == "2024-01-01T00:00:00Z"
    mock_client.get.assert_awaited_once()
    call_kwargs = mock_client.get.call_args
    assert "text" in call_kwargs.kwargs.get("params", {})


@pytest.mark.asyncio
async def test_search_npm_empty_results():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"objects": []}
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch(
        "src.main_graph.nodes.recommendation_tools.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from src.main_graph.nodes.recommendation_tools import _search_npm

        result = await _search_npm("nonexistent-xyz-package")

    assert result == []


# ── _get_npm_metadata ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_npm_metadata_extracts_fields():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "name": "express",
        "description": "Fast web framework",
        "dist-tags": {"latest": "4.18.2"},
        "license": "MIT",
        "homepage": "https://expressjs.com",
        "repository": {"url": "https://github.com/expressjs/express"},
        "maintainers": [{"name": "alice"}, {"name": "bob"}],
    }
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch(
        "src.main_graph.nodes.recommendation_tools.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from src.main_graph.nodes.recommendation_tools import _get_npm_metadata

        result = await _get_npm_metadata("express")

    assert result["name"] == "express"
    assert result["latest_version"] == "4.18.2"
    assert result["license"] == "MIT"
    assert result["maintainers"] == ["alice", "bob"]
    assert result["deprecated"] is None


# ── _get_github_summary ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_github_summary_returns_repo_info():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "stargazers_count": 12000,
        "open_issues_count": 45,
        "pushed_at": "2024-03-01T10:00:00Z",
        "archived": False,
    }
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch(
        "src.main_graph.nodes.recommendation_tools.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from src.main_graph.nodes.recommendation_tools import _get_github_summary

        result = await _get_github_summary("expressjs", "express")

    assert result["stars"] == 12000
    assert result["open_issues"] == 45
    assert result["archived"] is False


@pytest.mark.asyncio
async def test_get_github_summary_returns_empty_dict_on_http_error():
    import httpx

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
    )

    with patch(
        "src.main_graph.nodes.recommendation_tools.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from src.main_graph.nodes.recommendation_tools import _get_github_summary

        result = await _get_github_summary("nonexistent", "repo")

    assert result == {}


@pytest.mark.asyncio
async def test_get_github_summary_returns_empty_dict_on_timeout():
    import httpx

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(
        side_effect=httpx.TimeoutException("timeout")
    )

    with patch(
        "src.main_graph.nodes.recommendation_tools.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from src.main_graph.nodes.recommendation_tools import _get_github_summary

        result = await _get_github_summary("slow", "repo")

    assert result == {}


# ── _compare_packages ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compare_packages_returns_metadata_for_all():
    express_meta = {"name": "express", "latest_version": "4.18.2", "license": "MIT"}
    fastify_meta = {"name": "fastify", "latest_version": "4.0.0", "license": "MIT"}

    call_count = 0

    async def fake_get_npm_metadata(name: str) -> dict:
        nonlocal call_count
        call_count += 1
        return express_meta if name == "express" else fastify_meta

    with patch(
        "src.main_graph.nodes.recommendation_tools._get_npm_metadata",
        side_effect=fake_get_npm_metadata,
    ):
        from src.main_graph.nodes.recommendation_tools import _compare_packages

        result = await _compare_packages("express", ["fastify"])

    assert "express" in result
    assert "fastify" in result
    assert call_count == 2


@pytest.mark.asyncio
async def test_compare_packages_returns_fallback_on_failed_package():
    async def fake_get_npm_metadata(name: str) -> dict:
        if name == "broken":
            raise RuntimeError("fetch failed")
        return {"name": name, "latest_version": "1.0.0"}

    with patch(
        "src.main_graph.nodes.recommendation_tools._get_npm_metadata",
        side_effect=fake_get_npm_metadata,
    ):
        from src.main_graph.nodes.recommendation_tools import _compare_packages

        result = await _compare_packages("express", ["broken"])

    assert "express" in result
    assert result["express"]["latest_version"] == "1.0.0"
    assert result["broken"] == {"name": "broken"}  # fallback stub

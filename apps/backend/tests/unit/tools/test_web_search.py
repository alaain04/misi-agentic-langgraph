from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.tools.registry import TOOL_REGISTRY


@pytest.mark.asyncio
async def test_web_search_returns_results():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "results": [
            {
                "title": "lodash alternative",
                "url": "https://example.com",
                "content": "Use ramda instead",
            }
        ]
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=fake_response)

    with (
        patch("src.main_graph.tools.external_api.settings") as mock_settings,
        patch(
            "src.main_graph.tools.external_api.httpx.AsyncClient",
            return_value=mock_client,
        ),
    ):
        mock_settings.tavily_api_key = "test-key"
        result = await TOOL_REGISTRY["web_search"](
            package_name="lodash", query="alternatives npm"
        )

    assert result["query"] == "lodash alternatives npm"
    assert len(result["results"]) == 1
    assert result["results"][0]["url"] == "https://example.com"


@pytest.mark.asyncio
async def test_web_search_returns_error_when_no_api_key():
    with patch("src.main_graph.tools.external_api.settings") as mock_settings:
        mock_settings.tavily_api_key = ""
        result = await TOOL_REGISTRY["web_search"](
            package_name="test-pkg", query="test"
        )
    assert "error" in result
    assert result["results"] == []


@pytest.mark.asyncio
async def test_web_search_handles_http_error():
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

    with (
        patch("src.main_graph.tools.external_api.settings") as mock_settings,
        patch(
            "src.main_graph.tools.external_api.httpx.AsyncClient",
            return_value=mock_client,
        ),
    ):
        mock_settings.tavily_api_key = "test-key"
        result = await TOOL_REGISTRY["web_search"](
            package_name="test-pkg", query="test"
        )

    assert "error" in result
    assert result["results"] == []


def test_web_search_is_registered():
    assert "web_search" in TOOL_REGISTRY

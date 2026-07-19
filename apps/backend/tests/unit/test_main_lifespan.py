from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")


@pytest.mark.asyncio
async def test_lifespan_succeeds_when_mongo_and_codegraph_healthy():
    from src.main import lifespan

    mock_client = MagicMock()
    mock_client.admin.command = AsyncMock(return_value={"ok": 1})

    with (
        patch("src.main.get_client", return_value=mock_client),
        patch("src.main.DockerContainerAdapter") as mock_adapter_cls,
    ):
        mock_adapter_cls.return_value.run = AsyncMock(return_value=(0, "v1.0", ""))
        async with lifespan(MagicMock()):
            pass  # no exception means startup succeeded


@pytest.mark.asyncio
async def test_lifespan_raises_when_codegraph_image_broken():
    from src.main import lifespan

    mock_client = MagicMock()
    mock_client.admin.command = AsyncMock(return_value={"ok": 1})

    with (
        patch("src.main.get_client", return_value=mock_client),
        patch("src.main.DockerContainerAdapter") as mock_adapter_cls,
    ):
        mock_adapter_cls.return_value.run = AsyncMock(
            return_value=(1, "", "no such image")
        )
        with pytest.raises(RuntimeError, match="codegraph"):
            async with lifespan(MagicMock()):
                pass


@pytest.mark.asyncio
async def test_lifespan_raises_when_mongo_unreachable():
    from src.main import lifespan

    mock_client = MagicMock()
    mock_client.admin.command = AsyncMock(side_effect=RuntimeError("connection refused"))

    with patch("src.main.get_client", return_value=mock_client):
        with pytest.raises(RuntimeError, match="connection refused"):
            async with lifespan(MagicMock()):
                pass

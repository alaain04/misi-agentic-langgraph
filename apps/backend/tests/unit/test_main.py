from unittest.mock import AsyncMock, patch

import pytest

from src.main import lifespan


@pytest.mark.asyncio
async def test_lifespan_checks_trivy_image_runnable():
    app = AsyncMock()
    with (
        patch("src.main.get_client") as mock_get_client,
        patch("src.main.DockerContainerAdapter") as mock_adapter_cls,
    ):
        mock_get_client.return_value.admin.command = AsyncMock()
        mock_adapter = mock_adapter_cls.return_value
        mock_adapter.run = AsyncMock(
            side_effect=[(0, "", ""), (0, "Version: 0.71.2", "")]
        )

        async with lifespan(app):
            pass

    calls = mock_adapter.run.call_args_list
    images_checked = [c.kwargs.get("image") for c in calls]
    assert "trivy --version" in [c.kwargs.get("command") for c in calls]
    assert any("trivy" in str(img) for img in images_checked)
